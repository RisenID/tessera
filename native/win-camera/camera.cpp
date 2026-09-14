// tessera-camera.exe: puts the virtual camera up and feeds it NV12 frames.
//   tessera-camera.exe WIDTH HEIGHT [NAME]       frames on stdin
//   tessera-camera.exe --self-test DLL           checks TesseraCamera.dll in-process

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfvirtualcamera.h>
#include <ks.h>
#include <ksmedia.h>
#include <wrl/client.h>

#include <fcntl.h>
#include <io.h>
#include <stdio.h>
#include <vector>

#include "shared.h"

using Microsoft::WRL::ComPtr;

static const IID IID_KsControl = {0x28F54685, 0x06FD, 0x11D2, {0xB2, 0x7A, 0x00, 0xA0, 0xC9, 0x22, 0x31, 0x96}};

static void Report(const wchar_t* what, HRESULT hr) {
    wchar_t text[512] = L"";
    FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, hr, 0, text, 512, nullptr);
    fwprintf(stderr, L"%s (0x%08lX): %s\n", what, static_cast<unsigned long>(hr), text);
    fflush(stderr);
}

// -- writing frames ----------------------------------------------------------

class Writer {
public:
    ~Writer() {
        if (_view) UnmapViewOfFile(_view);
        if (_section) CloseHandle(_section);
    }

    void Write(const BYTE* frame, UINT32 width, UINT32 height) {
        // The section appears when an app first opens the camera.
        if (!_view && _tries++ % 15 == 0) Open();
        if (!_view) return;
        InterlockedIncrement64(&_view->sequence);
        _view->magic = FRAMES_MAGIC;
        _view->width = width;
        _view->height = height;
        memcpy(reinterpret_cast<BYTE*>(_view + 1), frame, static_cast<size_t>(width) * height * 3 / 2);
        InterlockedIncrement64(&_view->sequence);
    }

private:
    void Open() {
        for (const wchar_t* name : {FRAMES_GLOBAL, FRAMES_LOCAL}) {
            _section = OpenFileMappingW(FILE_MAP_WRITE, FALSE, name);
            if (_section) break;
        }
        if (_section) _view = static_cast<FrameHeader*>(MapViewOfFile(_section, FILE_MAP_WRITE, 0, 0, 0));
    }

    HANDLE _section = nullptr;
    FrameHeader* _view = nullptr;
    unsigned _tries = 0;
};

static bool ReadFrame(std::vector<BYTE>& frame) {
    size_t filled = 0;
    while (filled < frame.size()) {
        size_t got = fread(frame.data() + filled, 1, frame.size() - filled, stdin);
        if (got == 0) return false;
        filled += got;
    }
    return true;
}

static int Run(UINT32 width, UINT32 height, const wchar_t* name) {
    ComPtr<IMFVirtualCamera> camera;
    HRESULT hr = MFCreateVirtualCamera(MFVirtualCameraType_SoftwareCameraSource, MFVirtualCameraLifetime_Session,
                                       MFVirtualCameraAccess_CurrentUser, name, TESSERA_CAMERA_CLSID, nullptr, 0,
                                       &camera);
    if (FAILED(hr)) {
        Report(L"could not create the camera", hr);
        return 2;
    }
    hr = camera->Start(nullptr);
    if (FAILED(hr)) {
        Report(L"could not start the camera", hr);
        return 3;
    }
    fwprintf(stderr, L"camera started: %s %ux%u\n", name, width, height);
    fflush(stderr);

    _setmode(_fileno(stdin), _O_BINARY);
    std::vector<BYTE> frame(static_cast<size_t>(width) * height * 3 / 2);
    Writer writer;
    while (ReadFrame(frame)) writer.Write(frame.data(), width, height);

    camera->Remove();
    return 0;
}

// -- self test ---------------------------------------------------------------

static int Fail(const wchar_t* step, HRESULT hr) {
    Report(step, hr);
    return 1;
}

#define STEP(label, expr) do { HRESULT hr_ = (expr); if (FAILED(hr_)) return Fail(label, hr_); } while (0)

static HRESULT NextEvent(IMFMediaEventGenerator* from, MediaEventType wanted, IUnknown** value) {
    for (int i = 0; i < 6; ++i) {
        ComPtr<IMFMediaEvent> event;
        HRESULT hr = from->GetEvent(0, &event);
        if (FAILED(hr)) return hr;
        MediaEventType type = MEUnknown;
        event->GetType(&type);
        if (type != wanted) continue;
        if (value) {
            PROPVARIANT v;
            PropVariantInit(&v);
            event->GetValue(&v);
            if (v.vt == VT_UNKNOWN && v.punkVal) {
                *value = v.punkVal;
                v.punkVal->AddRef();
            }
            PropVariantClear(&v);
        }
        return S_OK;
    }
    return E_FAIL;
}

static int SelfTest(const wchar_t* path) {
    HMODULE dll = LoadLibraryW(path);
    if (!dll) return Fail(L"load the DLL", HRESULT_FROM_WIN32(GetLastError()));
    auto getClass = reinterpret_cast<HRESULT(STDAPICALLTYPE*)(REFCLSID, REFIID, LPVOID*)>(
        GetProcAddress(dll, "DllGetClassObject"));
    if (!getClass) return Fail(L"find DllGetClassObject", E_NOINTERFACE);

    ComPtr<IClassFactory> factory;
    STEP(L"class factory",
         getClass(CLSID_TesseraCamera, IID_IClassFactory, reinterpret_cast<LPVOID*>(factory.GetAddressOf())));
    ComPtr<IMFActivate> activate;
    STEP(L"activator", factory->CreateInstance(nullptr, IID_PPV_ARGS(&activate)));
    ComPtr<IMFMediaSource> source;
    STEP(L"activate the source", activate->ActivateObject(IID_PPV_ARGS(&source)));

    ComPtr<IMFMediaSourceEx> ex;
    ComPtr<IUnknown> ks;
    ComPtr<IMFSampleAllocatorControl> allocator;
    STEP(L"IMFMediaSourceEx", source.As(&ex));
    STEP(L"IKsControl", source->QueryInterface(IID_KsControl, &ks));
    STEP(L"IMFSampleAllocatorControl", source.As(&allocator));

    ComPtr<IMFPresentationDescriptor> presentation;
    STEP(L"presentation descriptor", source->CreatePresentationDescriptor(&presentation));
    DWORD streams = 0;
    presentation->GetStreamDescriptorCount(&streams);
    if (streams != 1) return Fail(L"one stream", E_UNEXPECTED);
    BOOL selected = FALSE;
    ComPtr<IMFStreamDescriptor> descriptor;
    STEP(L"stream descriptor", presentation->GetStreamDescriptorByIndex(0, &selected, &descriptor));
    GUID category = GUID_NULL;
    STEP(L"stream category", descriptor->GetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, &category));
    if (category != PINNAME_VIDEO_CAPTURE) return Fail(L"a capture stream", E_UNEXPECTED);

    ComPtr<IMFMediaTypeHandler> handler;
    ComPtr<IMFMediaType> type;
    STEP(L"media types", descriptor->GetMediaTypeHandler(&handler));
    STEP(L"current type", handler->GetCurrentMediaType(&type));
    GUID subtype = GUID_NULL;
    UINT32 width = 0, height = 0;
    type->GetGUID(MF_MT_SUBTYPE, &subtype);
    MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height);
    if (subtype != MFVideoFormat_NV12 || width != 1920 || height != 1080) return Fail(L"NV12 1080p first", E_UNEXPECTED);

    PROPVARIANT start;
    PropVariantInit(&start);
    STEP(L"start", source->Start(presentation.Get(), nullptr, &start));
    ComPtr<IUnknown> unknown;
    STEP(L"new stream event", NextEvent(source.Get(), MENewStream, &unknown));
    ComPtr<IMFMediaStream> stream;
    STEP(L"the stream", unknown.As(&stream));
    STEP(L"source started event", NextEvent(source.Get(), MESourceStarted, nullptr));
    STEP(L"stream started event", NextEvent(stream.Get(), MEStreamStarted, nullptr));

    // A frame, as the camera process writes it: 320x180, scaled up by the source.
    HANDLE section = nullptr;
    for (const wchar_t* name : {FRAMES_GLOBAL, FRAMES_LOCAL}) {
        section = OpenFileMappingW(FILE_MAP_WRITE, FALSE, name);
        if (section) break;
    }
    if (!section) return Fail(L"the frames section", HRESULT_FROM_WIN32(GetLastError()));
    auto view = static_cast<FrameHeader*>(MapViewOfFile(section, FILE_MAP_WRITE, 0, 0, 0));
    if (!view) return Fail(L"map the frames section", HRESULT_FROM_WIN32(GetLastError()));
    InterlockedIncrement64(&view->sequence);
    view->magic = FRAMES_MAGIC;
    view->width = 320;
    view->height = 180;
    BYTE* pixels = reinterpret_cast<BYTE*>(view + 1);
    memset(pixels, 200, 320 * 180);
    memset(pixels + 320 * 180, 90, 320 * 90);
    InterlockedIncrement64(&view->sequence);

    STEP(L"request a sample", stream->RequestSample(nullptr));
    ComPtr<IUnknown> delivered;
    STEP(L"sample event", NextEvent(stream.Get(), MEMediaSample, &delivered));
    ComPtr<IMFSample> sample;
    STEP(L"the sample", delivered.As(&sample));
    DWORD total = 0;
    sample->GetTotalLength(&total);
    if (total != 1920 * 1080 * 3 / 2) return Fail(L"a whole 1080p frame", E_UNEXPECTED);
    ComPtr<IMFMediaBuffer> buffer;
    STEP(L"sample buffer", sample->GetBufferByIndex(0, &buffer));
    BYTE* data = nullptr;
    DWORD length = 0;
    STEP(L"lock the buffer", buffer->Lock(&data, nullptr, &length));
    bool painted = data[0] == 200 && data[1920 * 1079 + 1919] == 200 && data[1920 * 1080] == 90;
    buffer->Unlock();
    if (!painted) return Fail(L"the frame written is the frame delivered", E_UNEXPECTED);

    UnmapViewOfFile(view);
    CloseHandle(section);
    source->Stop();
    source->Shutdown();
    activate->ShutdownObject();
    wprintf(L"self-test ok\n");
    return 0;
}

int wmain(int argc, wchar_t** argv) {
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) return Fail(L"COM", hr);
    hr = MFStartup(MF_VERSION);
    if (FAILED(hr)) return Fail(L"Media Foundation", hr);

    int code = 64;
    if (argc >= 3 && wcscmp(argv[1], L"--self-test") == 0) {
        code = SelfTest(argv[2]);
    } else if (argc >= 3) {
        UINT32 width = static_cast<UINT32>(_wtoi(argv[1]));
        UINT32 height = static_cast<UINT32>(_wtoi(argv[2]));
        if (!width || !height || width > MAX_WIDTH || height > MAX_HEIGHT || (width & 1) || (height & 1)) {
            fwprintf(stderr, L"bad size %ux%u\n", width, height);
        } else {
            code = Run(width, height, argc > 3 ? argv[3] : L"Tessera Camera");
        }
    } else {
        fwprintf(stderr, L"usage: tessera-camera.exe WIDTH HEIGHT [NAME] | --self-test DLL\n");
    }

    MFShutdown();
    CoUninitialize();
    return code;
}
