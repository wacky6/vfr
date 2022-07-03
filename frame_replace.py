import av
import sys
from tqdm import tqdm
import argparse
from pathlib import Path
from glob import glob
from PIL import Image
import re
from prefetch_generator import BackgroundGenerator, background


parser = argparse.ArgumentParser(description='Video frame replacer for video super resolution tasks')
parser.add_argument('-i', '--input', help='Path to the original video file, \
    whose video stream will be replaced by -r and other streams copied to output.')
parser.add_argument('-r', '--replacement', help='Glob pattern to replacement video frames. Frames will be sorted numerically. \
    Should yield the same amount of frames in the input video file.')
parser.add_argument('-o', '--output', help='Path to output file.')
parser.add_argument('-y', '--yes', help='Overwrite output if it exists.', default=False, action='store_true')
parser.add_argument('-vs', '--vscale', help='Scale factor to apply on the original video', default='1', type=float)
parser.add_argument('-vpix_fmt', '--vpix_fmt', help='Output pixel format', default='yuv420p')

# Basic argument sanity checks.
args = parser.parse_args()

in_container = av.open(args.input)
if len(in_container.streams.video) == 0:
    print(f"Can't find video stream in input file.", file=sys.stderr)
    sys.exit(1)
if len(in_container.streams.video) > 1:
    print(f"More than one video streams found in input file, found {len(in_container.video)}.", file=sys.stderr)
    sys.exit(1)

if Path(args.output).exists() and not args.yes:
    print(f"Output exists. Specify `-y` or `--yes` to overwrite it.", file=sys.stderr)
    sys.exit(1)

# Parse replacement frame descrpiton.
# TODO: Implement rawvideo stream replacement
# TODO: Parallelize image load.
replacement_gen = None
num_replacements = 0
if '*' in args.replacement:
    # Only support * glob to disambiguate.
    replacements = glob(args.replacement)
    if len(replacements) == 0:
        print(f"0 replacement frames found with glob pattern `{args.replacement}`.", file=sys.stderr)
        sys.exit(1)
    re_numerical = r'(\d+)'
    failed_guesses = []
    def guess_numerical(s):
        m = re.search(re_numerical, s)
        if m:
            return int(m.group(0))
        else:
            failed_guesses.append(s)
            return 0
    replacements = list(sorted(replacements, key=guess_numerical))
    num_replacements = len(replacements)
    if num_replacements > 0:
        print(f"Warning: frame ordering can't be determined for some inputs: {failed_guesses[:3]}.", file=sys.stderr)

    def glob_gen():
        for p in replacements:
            yield Image.open(p)

    replacement_gen = BackgroundGenerator(glob_gen(), max_prefetch=8)

elif args.replacement == '-':
    print(f"Pipe replacement not implemented", file=sys.stderr)
    sys.exit(2)
else:
    print(f"Unsupported replacement method", file=sys.stderr)


# Probe input
ivs = in_container.streams.video[0]
ivcc = ivs.codec_context
total_frames = ivs.frames if ivs.frames is not None else 0
print(f'input video:')
print(f'  start_time: {ivs.start_time}')
print(f'  time_base: {ivs.time_base}')
print(f'  base_rate: {ivs.base_rate}')
print(f'  frames: {total_frames if total_frames > 0 else "Unknown"}')
print(f'  width: {ivcc.width}')
print(f'  width: {ivcc.height}')
print('')

# Check the number of replacement frames and input frames match if both known
if total_frames > 0 and num_replacements > 0 and total_frames != num_replacements:
    print(f"Warning: number of replacements doesn't match: frames = {total_frames}, replacements = {num_replacements}")

# Create output file and initializes output streams.
out_container = av.open(args.output, 'w')
ostreams = []
ovs = None
for s in in_container.streams.get():
    if s.type == 'video':
        # Video stream is modified (e.g. upscaled)
        ovs = out_container.add_stream('hevc_nvenc', None)
        ovs.width = int(ivcc.width * args.vscale)
        ovs.height = int(ivcc.height * args.vscale)
        ovs.time_base = ivs.time_base
        ovs.pix_fmt = args.vpix_fmt

        ostreams.append(ovs)
    else:
        # Non-video streams are copied
        ostreams.append(out_container.add_stream(template = s))

# Start replacement.
ovreformatter = av.video.reformatter.VideoReformatter()
n_frame = 1

@background(max_prefetch=8)
def oframe_generator(replacement_gen):
    for rimage in replacement_gen:
        oframe = av.VideoFrame.from_image(rimage)
        oframe = ovreformatter.reformat(
            oframe,
            format=ovs.pix_fmt,
            width=ovs.width, height=ovs.height,
            interpolation='LANCZOS'
        )
        yield oframe

oframe_gen = oframe_generator(replacement_gen)

pbar = tqdm(total = total_frames)
for packet in in_container.demux():
    if packet.dts is None:
        # Skip flushing packets.
        continue

    if packet.stream.type == 'video':
        for iframe in packet.decode():
            try:
                oframe = next(oframe_gen)
                oframe.pts = iframe.pts
            except Exception as e:
                print(f"Run out of replacement frames at frame_index {n_frame}.", file=sys.stderr)
                print(e, file=sys.stderr)
                sys.exit(2)

            for packet in ovs.encode(oframe):
                out_container.mux(packet)

            n_frame += 1
            pbar.update()
    else:
        # Non-video stream is copied to output
        packet.stream = ostreams[packet.stream.index]
        out_container.mux(packet)

in_container.close()
out_container.close()

# Check replacement frames are exhausted.
try:
    next(replacement_gen)
    printf(f"Warning: not all replacement frames are used.", file=sys.stderr)
except:
    pass