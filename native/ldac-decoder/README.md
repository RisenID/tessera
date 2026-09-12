# LDAC, received

Stock PipeWire on any distribution can send LDAC to headphones and cannot
accept it from a phone. This directory is the missing half.

## Why it is missing

A2DP codecs are directional. To *send* a codec you need an encoder; to
*receive* it you need a decoder, and the two ship separately. Sony open-sourced
the LDAC encoder (`libldacBT_enc`, packaged on Fedora as `libldac`) and never
released a decoder, so the endpoints PipeWire registers with BlueZ look like
this:

```
/MediaEndpoint/A2DPSource/ldac      <- we can send it
/MediaEndpoint/A2DPSink/aptx_hd     <- we can receive these
/MediaEndpoint/A2DPSink/aptx
/MediaEndpoint/A2DPSink/aac
/MediaEndpoint/A2DPSink/sbc
```

A phone can only choose a codec the sink has offered, so on a stock system the
best a Galaxy will send is aptX: 44.1 kHz, 16 bit, 352 kbit/s.

PipeWire itself is not the obstacle. `spa/plugins/bluez5/a2dp-codec-ldac.c`
contains a complete decode path — `codec_start_decode`, `codec_decode`, the
handle management, the lot — wrapped in `#ifdef ENABLE_LDAC_DEC`, and
`spa/meson.build` turns that on when it finds a library exporting
`ldacBT_decode`. Nobody has one, so nobody builds it.

## What is here

`ldacBT_dec.c` is that library, written against
[libldacdec](https://github.com/hegdi/libldacdec) — a clean-room LDAC decoder
(LDAC is close to a streaming-only ATRAC9), vendored at
`third_party/libldacdec`. It implements exactly the two entry points PipeWire
declares:

```c
int ldacBT_init_handle_decode(HANDLE_LDAC_BT, int channel_mode, int frequency,
                              int, int, int);
int ldacBT_decode(HANDLE_LDAC_BT, unsigned char *src, unsigned char *dst,
                  LDACBT_SMPL_FMT_T fmt, int src_size,
                  int *consumed, int *dst_out);
```

The handle is the awkward part. In Sony's library one `HANDLE_LDAC_BT` holds
both encoder and decoder state, so PipeWire allocates its decode handle with
`ldacBT_get_handle()` — from the *encoder* library, whose contents are opaque
to us. Decoder state therefore lives in a small table here, keyed by that
pointer and claimed by `ldacBT_init_handle_decode()`, which PipeWire always
calls immediately after allocating.

`scripts/build-ldac-decoder.sh` builds it, rebuilds PipeWire's LDAC codec
plugin from the matching release with `-DENABLE_LDAC_DEC`, and installs both
under `~/.local/lib64/tessera`. Tessera runs that script itself from
**Settings → Bluetooth audio**, so none of this needs a terminal; `tessera/
backends/ldacdec.py` is the part that finds it and checks the build
dependencies first. WirePlumber is pointed at them by prepending
that directory to `SPA_PLUGIN_DIR` in a systemd user drop-in, which replaces
exactly one plugin and leaves the other thirteen alone. Nothing owned by the
package manager is touched, and `--uninstall` removes all of it.

## Correctness

Verified by round trip against Sony's own encoder: a known two-tone signal
encoded with `libldacBT_enc` and decoded back through this library.

| Rate     | Quality mode | Frames | Failed | Correlation | SNR      |
|----------|--------------|--------|--------|-------------|----------|
| 44.1 kHz | HQ           | 344    | 0      | 1.00000     | 103.5 dB |
| 48 kHz   | HQ           | 374    | 0      | 1.00000     | 101.7 dB |
| 96 kHz   | HQ           | 374    | 0      | 1.00000     | 94.9 dB  |
| 96 kHz   | SQ           | 375    | 0      | 1.00000     | 92.0 dB  |
| 96 kHz   | MQ           | 372    | 0      | 1.00000     | 89.1 dB  |

## Two limits worth knowing

**Output is 16-bit.** libldacdec decodes to `int16_t`. When PipeWire asks for
S24, S32 or F32 the samples are widened exactly — the low bits are zero rather
than invented — so the audible result is 16-bit however it is labelled. The
gain over aptX is in bandwidth and sample rate (909 kbit/s at up to 96 kHz
against 352 at 44.1), not in depth.

**Dual-channel LDAC is refused.** That mode splits a stereo pair across two
independently coded blocks, and libldacdec's frame loop decodes every channel
inside each block and writes them all to the start of the output, so the second
block overwrites the first. Rather than emit audio that is not what was sent,
`ldacBT_decode` returns an error for it. Phones negotiate stereo; this exists
so that a device which does not is silent rather than wrong.

## After a PipeWire upgrade

Re-run the script. `libspa-bluez5.so` refuses any codec plugin built against a
different `SPA_VERSION_BLUEZ5_CODEC_MEDIA`, so the plugin is tied to the
release it was built from. If it is ever refused, LDAC simply stops being
offered and the phone falls back — no crash, no silence.
