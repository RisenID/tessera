// Shared by TesseraCamera.dll and tessera-camera.exe.
#pragma once

#include <windows.h>

// {7D3A4C1E-5B2F-4E8A-9C61-2F0B8D4E7A19}
static const GUID CLSID_TesseraCamera = {
    0x7d3a4c1e, 0x5b2f, 0x4e8a, {0x9c, 0x61, 0x2f, 0x0b, 0x8d, 0x4e, 0x7a, 0x19}};
#define TESSERA_CAMERA_CLSID L"{7D3A4C1E-5B2F-4E8A-9C61-2F0B8D4E7A19}"

// Frame Server runs in session 0, so frames cross in a Global section. Local
// is for a source loaded in the user's own session, as the self test does.
#define FRAMES_GLOBAL L"Global\\TesseraCameraFrames"
#define FRAMES_LOCAL L"Local\\TesseraCameraFrames"

constexpr UINT32 FRAMES_MAGIC = 0x4D414354;  // "TCAM"
constexpr UINT32 MAX_WIDTH = 3840;
constexpr UINT32 MAX_HEIGHT = 2160;

// Followed by one NV12 frame. sequence is odd while a frame is being written.
struct FrameHeader {
    UINT32 magic;
    UINT32 width;
    UINT32 height;
    UINT32 reserved;
    LONG64 sequence;
};

inline ULONGLONG FramesSize() {
    return sizeof(FrameHeader) + static_cast<ULONGLONG>(MAX_WIDTH) * MAX_HEIGHT * 3 / 2;
}
