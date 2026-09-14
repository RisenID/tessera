#!/usr/bin/env python3
"""Checks the LDAC decoder shim against real frames and hostile ones.

Builds native/ldac-decoder/ldacBT_dec.c with libldacdec, encodes a tone with
Sony's encoder, and decodes it. Then feeds truncated, bad-header and corrupt
frames that end right before an unreadable page, so any read past the buffer
crashes the test rather than passing unnoticed.

    python3 scripts/check-ldac-decoder.py [--shim path/to/ldacBT_dec.c]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LDACDEC = ROOT / "third_party" / "libldacdec"
UNITS = ("libldacdec", "bit_allocation", "huffCodes", "bit_reader", "utility", "imdct", "spectrum")

TEST = r"""
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/mman.h>
#include <unistd.h>
#include <ldacBT.h>

int ldacBT_init_handle_decode(HANDLE_LDAC_BT, int, int, int, int, int);
int ldacBT_decode(HANDLE_LDAC_BT, unsigned char *, unsigned char *, LDACBT_SMPL_FMT_T,
                  int, int *, int *);

static int failures;
static void check(const char *label, int ok)
{
    printf("%s %s\n", ok ? "ok  " : "FAIL", label);
    fflush(stdout);
    if (!ok)
        failures++;
}

/* Memory whose last byte sits right before a PROT_NONE page. */
static unsigned char *at_page_end(const unsigned char *data, size_t size)
{
    long page = sysconf(_SC_PAGESIZE);
    unsigned char *map = mmap(NULL, (size_t)page * 2, PROT_READ | PROT_WRITE,
                              MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    mprotect(map + page, (size_t)page, PROT_NONE);
    unsigned char *start = map + page - size;
    memcpy(start, data, size);
    return start;
}

static int decode(HANDLE_LDAC_BT h, unsigned char *src, int size, int *used, int *wrote)
{
    static unsigned char dst[512 * 4];
    return ldacBT_decode(h, src, dst, LDACBT_SMPL_FMT_S16, size, used, wrote);
}

int main(void)
{
    signal(SIGSEGV, SIG_DFL);

    HANDLE_LDAC_BT enc = ldacBT_get_handle();
    if (ldacBT_init_handle_encode(enc, 679, LDACBT_EQMID_HQ, LDACBT_CHANNEL_MODE_STEREO,
                                  LDACBT_SMPL_FMT_S16, 48000) != 0) {
        printf("FAIL encoder init\n");
        return 1;
    }
    unsigned char stream[65536];
    int stream_len = 0;
    int16_t pcm[256];
    for (int block = 0; block < 400 && stream_len < 60000; ++block) {
        for (int i = 0; i < 128; ++i) {
            int16_t v = (int16_t)(12000 * sin(2 * M_PI * 440 * (block * 128 + i) / 48000.0));
            pcm[i * 2] = pcm[i * 2 + 1] = v;
        }
        int used = 0, wrote = 0, frames = 0;
        unsigned char packet[1024];
        ldacBT_encode(enc, pcm, &used, packet, &wrote, &frames);
        if (wrote > 0) {
            memcpy(stream + stream_len, packet, (size_t)wrote);
            stream_len += wrote;
        }
    }
    check("the encoder produced frames", stream_len > 0 && stream[0] == 0xAA);

    HANDLE_LDAC_BT dec = ldacBT_get_handle();
    check("the decoder initialises", ldacBT_init_handle_decode(dec, 1, 48000, 0, 0, 0) == 0);

    int position = 0, frames = 0, bad = 0, loud = 0;
    static unsigned char out[512 * 4];
    while (position + 3 <= stream_len && stream[position] == 0xAA) {
        int used = 0, wrote = 0;
        if (ldacBT_decode(dec, stream + position, out, LDACBT_SMPL_FMT_S16,
                          stream_len - position, &used, &wrote) != 0 || used <= 0) {
            bad++;
            break;
        }
        for (int i = 0; i + 1 < wrote; i += 2)
            if (abs((int16_t)(out[i] | out[i + 1] << 8)) > 1000)
                loud = 1;
        position += used;
        frames++;
    }
    check("real frames decode", frames > 50 && bad == 0);
    check("into audible PCM", loud);

    int first = (((stream[1] & 0x7) << 6) | (stream[2] >> 2)) + 1 + 3;

    /* Exactly one frame, ending at a guard page. */
    int used = -1, wrote = -1;
    unsigned char *edge = at_page_end(stream, (size_t)first);
    check("a frame ending at unreadable memory decodes without reading past it",
          decode(dec, edge, first, &used, &wrote) == 0 && used == first);

    /* Truncated: the declared frame runs into the guard page. */
    edge = at_page_end(stream, (size_t)first - 1);
    used = wrote = -1;
    check("a truncated frame is refused before decoding",
          decode(dec, edge, first - 1, &used, &wrote) == -1 && used == 0 && wrote == 0);
    edge = at_page_end(stream, 2);
    check("a lone partial header is refused", decode(dec, edge, 2, &used, &wrote) == -1);

    /* Headers that index libldacdec's tables out of range. */
    unsigned char frame[1024];
    memcpy(frame, stream, (size_t)first);
    frame[1] = (unsigned char)((frame[1] & 0x1f) | (5 << 5));
    edge = at_page_end(frame, (size_t)first);
    check("an unknown sample rate is refused", decode(dec, edge, first, &used, &wrote) == -1);

    memcpy(frame, stream, (size_t)first);
    frame[1] = (unsigned char)((frame[1] & ~0x18) | (3 << 3));
    edge = at_page_end(frame, (size_t)first);
    check("an unknown channel config is refused", decode(dec, edge, first, &used, &wrote) == -1);

    memcpy(frame, stream, (size_t)first);
    frame[1] = (unsigned char)((frame[1] & ~0x18) | (1 << 3));
    edge = at_page_end(frame, (size_t)first);
    check("dual-channel is refused", decode(dec, edge, first, &used, &wrote) == -1);

    memcpy(frame, stream, (size_t)first);
    frame[0] = 0x55;
    edge = at_page_end(frame, (size_t)first);
    check("a missing sync byte is refused", decode(dec, edge, first, &used, &wrote) == -1);

    /* Random frame bodies are not tested: libldacdec itself asserts on some. */

    ldacBT_free_handle(enc);
    printf(failures ? "%d failed\n" : "all LDAC decoder checks passed\n", failures);
    return failures ? 1 : 0;
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shim", type=Path, default=ROOT / "native" / "ldac-decoder" / "ldacBT_dec.c")
    shim = parser.parse_args().shim

    missing = [name for name, ok in (
        ("gcc", shutil.which("gcc")),
        ("ldacBT.h (libldac-devel)", Path("/usr/include/ldacBT.h").exists()
         or Path("/usr/include/ldac/ldacBT.h").exists()),
        ("libldacdec sources", (LDACDEC / "libldacdec.c").exists()),
    ) if not ok]
    if missing:
        print(f"skipped: missing {', '.join(missing)}")
        return 0

    with tempfile.TemporaryDirectory() as work:
        work_path = Path(work)
        (work_path / "test.c").write_text(TEST)
        include = ["-I", str(LDACDEC), "-I", "/usr/include/ldac"]
        objects = []
        for unit in UNITS:
            obj = work_path / f"{unit}.o"
            subprocess.run(["gcc", "-O2", "-std=gnu11", "-fPIC", "-w", "-c",
                            str(LDACDEC / f"{unit}.c"), *include, "-o", str(obj)], check=True)
            objects.append(str(obj))
        build = subprocess.run(
            ["gcc", "-O2", "-std=gnu11", "-Wall", "-Wextra", str(shim),
             str(work_path / "test.c"), *objects, *include,
             "-lldacBT_enc", "-lm", "-lpthread", "-o", str(work_path / "test")],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            print("FAIL the shim does not build\n" + build.stderr)
            return 1
        run = subprocess.run([str(work_path / "test")], capture_output=True, text=True, timeout=120)
        print(run.stdout, end="")
        if run.returncode < 0:
            print(f"FAIL crashed with signal {-run.returncode}")
            if run.stderr.strip():
                print(run.stderr.strip()[-800:])
            return 1
        return run.returncode


if __name__ == "__main__":
    sys.exit(main())
