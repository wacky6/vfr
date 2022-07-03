import av

"""
Returns `(codec_name, options)` based on currently available codecs.

In order of preference:
- hevc_nvenc
"""
def pick_best_codec(out_width, out_height, pix_fmt):
    if 'hevc_nvenc' in av.codecs_available:
        return (
            'hevc_nvenc',
            {
                'profile': 'main',
                'preset': 'slow',
                'rc': 'vbr',
                'cq': '17',
            }
        )

    # TODO: add more codec configs

    raise Execption('No preferred codec found')


