import av
import sys
from tqdm import tqdm
import argparse
from pathlib import Path
from glob import glob
from codec_options import pick_best_codec
import re

parser = argparse.ArgumentParser(description='Video frame replacer for video super resolution tasks')
parser.add_argument('-i', '--input', required=True, help='Path to the original video file, \
    whose video stream will be replaced by -r and other streams copied to output.')
parser.add_argument('-r', '--replacement', required=True, help='FFmpeg input pattern to replacement video frames. \
    Should contains the same number of video frames as the input file.')
parser.add_argument('-o', '--output', required=True, help='Path to output file.')
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

# Probe input
ivs = in_container.streams.video[0]
ivs.thread_type = 'AUTO'
ivcc = ivs.codec_context
total_frames = ivs.frames if ivs.frames is not None else 0
print(f'input video:')
print(f'  start_time: {ivs.start_time}')
print(f'  time_base: {ivs.time_base}')
print(f'  base_rate: {ivs.base_rate}')
print(f'  frames: {total_frames if total_frames > 0 else "Unknown"}')
print(f'  width: {ivcc.width}')
print(f'  height: {ivcc.height}')
print('')

# Create output file and initializes output streams.
out_container = av.open(args.output, 'w')
ostreams = []
ovs = None
for s in in_container.streams.get():
    if s.type == 'video':
        out_width = int(ivcc.width * args.vscale)
        out_height = int(ivcc.height * args.vscale)
        # Video stream is modified (e.g. upscaled)
        name, opts = pick_best_codec(out_width, out_height, args.vpix_fmt)
        ovs = out_container.add_stream(name, options=opts)
        ovs.width = out_width
        ovs.height = out_height
        ovs.time_base = ivs.time_base
        ovs.pix_fmt = args.vpix_fmt

        ostreams.append(ovs)
    else:
        # Non-video streams are copied
        ostreams.append(out_container.add_stream(template = s))

# Start replacement.
ovreformatter = av.video.reformatter.VideoReformatter()
n_frame = 1

# Parse replacement frame descrpiton.
# TODO: Implement rawvideo stream replacement
def generate_replacement_frames():
    replacement_container = av.open(args.replacement)
    rvs = replacement_container.streams.video[0]
    rvs.thread_type = 'AUTO'

    # Filter to load replacements into desired output size (width, height)
    graph = av.filter.Graph()
    f_inp_buf = graph.add_buffer(template=rvs)
    f_scale = graph.add("scale", f'{ovs.height}:{ovs.width}:lanczos')
    f_pix_fmt = graph.add("format", f'pix_fmts={ovs.pix_fmt}')
    f_sink = graph.add('buffersink')
    f_inp_buf.link_to(f_scale)
    f_scale.link_to(f_pix_fmt)
    f_pix_fmt.link_to(f_sink)
    graph.configure()

    for frame in replacement_container.decode(video=0):
        f_inp_buf.push(frame)
        yield f_sink.pull()

replacement_gen = generate_replacement_frames()

pbar = tqdm(total = total_frames)
for packet in in_container.demux():
    if packet.dts is None:
        # Skip flushing packets.
        continue

    if packet.stream.type == 'video':
        for iframe in packet.decode():
            try:
                oframe = next(replacement_gen)
                oframe.pts = iframe.pts
                oframe.time_base = iframe.time_base
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