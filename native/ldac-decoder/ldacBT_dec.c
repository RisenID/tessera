/*
 * libldacBT_dec -- the decoder half of Sony's LDAC ABI, over libldacdec.
 *
 * PipeWire already knows how to receive LDAC. spa/plugins/bluez5/
 * a2dp-codec-ldac.c has a full decode path behind ENABLE_LDAC_DEC, which its
 * build turns on only when a library providing ldacBT_decode() is present.
 * Sony open-sourced the LDAC *encoder* and nothing else, so no distribution
 * has ever had one: Fedora's pipewire-libs registers /MediaEndpoint/
 * A2DPSource/ldac and no A2DPSink counterpart, which is why a phone cannot
 * offer LDAC to this computer.
 *
 * libldacdec (github.com/hegdi/libldacdec) is a clean-room decoder -- LDAC is
 * a stripped-down ATRAC9 -- but it exposes its own small API rather than
 * Sony's. This file is the adapter: the two entry points PipeWire declares,
 * implemented on top of it.
 *
 *      int ldacBT_init_handle_decode(HANDLE_LDAC_BT, int channel_mode,
 *                                    int frequency, int, int, int);
 *      int ldacBT_decode(HANDLE_LDAC_BT, unsigned char *src,
 *                        unsigned char *dst, LDACBT_SMPL_FMT_T fmt,
 *                        int src_size, int *consumed, int *dst_out);
 *
 * The handle is the awkward part. In Sony's library one HANDLE_LDAC_BT holds
 * both encoder and decoder state, so PipeWire allocates its decode handle with
 * ldacBT_get_handle() -- from the *encoder* library, which knows nothing about
 * us and whose contents are opaque. Decoder state therefore lives here, in a
 * small table keyed by that pointer. Entries are claimed by
 * ldacBT_init_handle_decode(), which PipeWire always calls immediately after
 * allocating a handle, so a pointer recycled by malloc from a freed handle is
 * reset rather than reused stale.
 *
 * SPDX-License-Identifier: MIT
 */

#include <pthread.h>
#include <stdint.h>
#include <string.h>

#include <ldacBT.h>

#include "ldacdec.h"

/* Two at a time is already generous: one A2DP transport, one in teardown. */
#define MAX_DECODERS 8

/* The largest frame libldacdec can produce: 256 samples, two channels. */
#define MAX_FRAME_PCM (MAX_FRAME_SAMPLES * 2)

/* Sync byte, then sample rate (3 bits), channel config (2), length - 1 (9), status (2). */
#define HEADER_BYTES 3
#define MAX_FRAME_BYTES (512 + HEADER_BYTES)

/*
 * libldacdec's bit reader has no bounds, and a malformed frame can steer it
 * about 2 KB past the header (16-bit words, twice, for 512 samples). Frames are
 * decoded from a zero-padded copy so it can never read the caller's memory.
 */
#define DECODE_PAD 4096

struct slot {
    HANDLE_LDAC_BT handle;
    ldacdec_t decoder;
    int in_use;
    unsigned long long used_at;
};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct slot slots[MAX_DECODERS];
static unsigned long long clock_tick;

static struct slot *find(HANDLE_LDAC_BT handle)
{
    for (int i = 0; i < MAX_DECODERS; ++i)
        if (slots[i].in_use && slots[i].handle == handle)
            return &slots[i];
    return NULL;
}

/*
 * A free slot, or the one left untouched longest.
 *
 * PipeWire has no way to tell us a handle was freed -- ldacBT_free_handle()
 * belongs to the encoder library -- so the table would otherwise fill up over
 * a long session of connects and disconnects. Evicting the oldest bounds it
 * without ever discarding the transport that is actually streaming.
 */
static struct slot *claim(HANDLE_LDAC_BT handle)
{
    struct slot *chosen = find(handle);

    if (chosen == NULL)
        for (int i = 0; i < MAX_DECODERS; ++i)
            if (!slots[i].in_use) {
                chosen = &slots[i];
                break;
            }

    if (chosen == NULL) {
        chosen = &slots[0];
        for (int i = 1; i < MAX_DECODERS; ++i)
            if (slots[i].used_at < chosen->used_at)
                chosen = &slots[i];
    }

    memset(chosen, 0, sizeof(*chosen));
    chosen->handle = handle;
    chosen->in_use = 1;
    chosen->used_at = ++clock_tick;
    return chosen;
}

int ldacBT_init_handle_decode(HANDLE_LDAC_BT handle, int channel_mode,
                              int frequency, int dummy1, int dummy2, int dummy3);
int ldacBT_decode(HANDLE_LDAC_BT handle, unsigned char *src, unsigned char *dst,
                  LDACBT_SMPL_FMT_T fmt, int src_size, int *consumed, int *dst_out);

int ldacBT_init_handle_decode(HANDLE_LDAC_BT handle, int channel_mode,
                              int frequency, int dummy1, int dummy2, int dummy3)
{
    (void)channel_mode;   /* Every frame carries its own configuration. */
    (void)frequency;
    (void)dummy1;
    (void)dummy2;
    (void)dummy3;

    if (handle == NULL)
        return -1;

    pthread_mutex_lock(&lock);
    struct slot *slot = claim(handle);
    int res = ldacdecInit(&slot->decoder);
    if (res < 0)
        slot->in_use = 0;
    pthread_mutex_unlock(&lock);

    return res < 0 ? -1 : 0;
}

/*
 * libldacdec decodes to 16-bit samples; PipeWire asks for whatever it
 * negotiated. Widening is exact -- the low bits are zero rather than invented
 * -- so the audible result is the decoder's own 16-bit output either way.
 */
static int widen(const int16_t *pcm, int samples, LDACBT_SMPL_FMT_T fmt, unsigned char *dst)
{
    switch (fmt) {
    case LDACBT_SMPL_FMT_S16:
        memcpy(dst, pcm, (size_t)samples * 2);
        return samples * 2;
    case LDACBT_SMPL_FMT_S24:
        for (int i = 0; i < samples; ++i) {
            dst[i * 3 + 0] = 0;
            dst[i * 3 + 1] = (unsigned char)(pcm[i] & 0xff);
            dst[i * 3 + 2] = (unsigned char)((pcm[i] >> 8) & 0xff);
        }
        return samples * 3;
    case LDACBT_SMPL_FMT_S32:
        for (int i = 0; i < samples; ++i) {
            int32_t value = (int32_t)pcm[i] << 16;
            memcpy(dst + i * 4, &value, 4);
        }
        return samples * 4;
    case LDACBT_SMPL_FMT_F32:
        for (int i = 0; i < samples; ++i) {
            float value = (float)pcm[i] / 32768.0f;
            memcpy(dst + i * 4, &value, 4);
        }
        return samples * 4;
    default:
        return -1;
    }
}

/*
 * The frame's size in bytes, or -1 when its header is one libldacdec would
 * index its tables out of range with, or one this shim refuses.
 */
static int frame_bytes(const unsigned char *src, int src_size)
{
    if (src_size < HEADER_BYTES || src[0] != 0xAA)
        return -1;

    int rate_id = src[1] >> 5;
    int config_id = (src[1] >> 3) & 0x3;
    int length = (((src[1] & 0x7) << 6) | (src[2] >> 2)) + 1;

    /* Four sample rates exist. Config 3 has no channel count. */
    if (rate_id > 3 || config_id > 2)
        return -1;

    /*
     * Dual-channel (config 1) is refused: libldacdec decodes both of its blocks
     * to the start of the output, so the second overwrites the first.
     */
    if (config_id == 1)
        return -1;

    return length + HEADER_BYTES;
}

int ldacBT_decode(HANDLE_LDAC_BT handle, unsigned char *src, unsigned char *dst,
                  LDACBT_SMPL_FMT_T fmt, int src_size, int *consumed, int *dst_out)
{
    int16_t pcm[MAX_FRAME_PCM];
    unsigned char padded[MAX_FRAME_BYTES + DECODE_PAD];
    int used = 0;

    if (consumed)
        *consumed = 0;
    if (dst_out)
        *dst_out = 0;

    if (handle == NULL || src == NULL || dst == NULL || src_size <= 0)
        return -1;

    /* Everything the decoder reads is checked before it runs. */
    int needed = frame_bytes(src, src_size);
    if (needed < 0 || needed > src_size)
        return -1;
    memcpy(padded, src, (size_t)needed);
    memset(padded + needed, 0, sizeof(padded) - (size_t)needed);

    pthread_mutex_lock(&lock);

    struct slot *slot = find(handle);
    if (slot == NULL) {
        pthread_mutex_unlock(&lock);
        return -1;
    }
    slot->used_at = ++clock_tick;

    if (ldacDecode(&slot->decoder, padded, pcm, &used) < 0) {
        pthread_mutex_unlock(&lock);
        return -1;
    }

    const frame_t *frame = &slot->decoder.frame;
    int channels = frame->channelCount;
    int samples = frame->frameSamples * channels;

    /* A frame whose contents ran past its own declared length is corrupt. */
    if (channels < 1 || channels > 2 || samples > MAX_FRAME_PCM || used != needed) {
        pthread_mutex_unlock(&lock);
        return -1;
    }

    pthread_mutex_unlock(&lock);

    int written = widen(pcm, samples, fmt, dst);
    if (written < 0)
        return -1;

    if (consumed)
        *consumed = used;
    if (dst_out)
        *dst_out = written;
    return 0;
}

/* What a frame was carrying, for callers that want to report it. */
int ldacBT_dec_shim_sample_rate(HANDLE_LDAC_BT handle)
{
    pthread_mutex_lock(&lock);
    struct slot *slot = find(handle);
    int rate = slot ? ldacdecGetSampleRate(&slot->decoder) : 0;
    pthread_mutex_unlock(&lock);
    return rate;
}
