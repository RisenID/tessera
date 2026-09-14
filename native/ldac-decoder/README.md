# LDAC decoder

PipeWire can send LDAC but not receive it: Sony released only the encoder.
PipeWire's LDAC plugin already has a decode path behind `ENABLE_LDAC_DEC`,
enabled when a library exports `ldacBT_decode`. This is that library, built on
the clean-room [libldacdec](https://github.com/hegdi/libldacdec)
(`third_party/libldacdec`).

It implements PipeWire's two entry points:

```c
int ldacBT_init_handle_decode(HANDLE_LDAC_BT, int channel_mode, int frequency, int, int, int);
int ldacBT_decode(HANDLE_LDAC_BT, unsigned char *src, unsigned char *dst,
                  LDACBT_SMPL_FMT_T fmt, int src_size, int *consumed, int *dst_out);
```

PipeWire allocates the handle with the encoder library's `ldacBT_get_handle()`,
so decoder state lives in a table here keyed by that pointer.

## Install

`scripts/build-ldac-decoder.sh` (or *Settings → Bluetooth audio*) builds this,
rebuilds PipeWire's LDAC plugin with the decoder enabled, and installs both in
`~/.local/lib64/tessera`, prepended to WirePlumber's `SPA_PLUGIN_DIR` via a
systemd user drop-in. `--uninstall` removes everything. Re-run after PipeWire
upgrades; a mismatched plugin is simply not loaded.

## Verified

Round trip through Sony's encoder:

| Rate | Mode | Frames | Failed | SNR |
| --- | --- | --- | --- | --- |
| 44.1 kHz | HQ | 344 | 0 | 103.5 dB |
| 48 kHz | HQ | 374 | 0 | 101.7 dB |
| 96 kHz | HQ | 374 | 0 | 94.9 dB |
| 96 kHz | SQ | 375 | 0 | 92.0 dB |
| 96 kHz | MQ | 372 | 0 | 89.1 dB |

## Limits

- Output is 16-bit; wider formats are zero-padded.
- Dual-channel mode is rejected (libldacdec overwrites the first block).
- The header and frame length are validated before decoding, and frames are
  decoded from a zero-padded copy: libldacdec's bit reader has no bounds.
- libldacdec still asserts on some corrupt frame bodies (`calculatePrecisions`).

`python3 scripts/check-ldac-decoder.py` tests this against Sony's encoder.
