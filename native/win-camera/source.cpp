// TesseraCamera.dll: the virtual camera's media source, loaded by Frame Server.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mfvirtualcamera.h>
#include <ks.h>
#include <ksmedia.h>
#include <sddl.h>
#include <wrl/client.h>
#include <wrl/implements.h>

#include <mutex>
#include <string>
#include <vector>

#include "shared.h"

using Microsoft::WRL::ChainInterfaces;
using Microsoft::WRL::ClassicCom;
using Microsoft::WRL::ComPtr;
using Microsoft::WRL::MakeAndInitialize;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;

MIDL_INTERFACE("28F54685-06FD-11D2-B27A-00A0C9223196")
IKsControl : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG*) = 0;
    virtual HRESULT STDMETHODCALLTYPE KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG*) = 0;
    virtual HRESULT STDMETHODCALLTYPE KsEvent(PKSEVENT, ULONG, LPVOID, ULONG, ULONG*) = 0;
};

#define CHECK(expr) do { HRESULT hr_ = (expr); if (FAILED(hr_)) return hr_; } while (0)

static HMODULE g_module;

namespace {

struct Size {
    UINT32 width, height;
};

// Offered to apps; frames are scaled to whichever they pick.
const Size SIZES[] = {{1920, 1080}, {1280, 720}, {960, 540}, {640, 360}};
const UINT32 FPS = 30;
const MFTIME FRAME_TIME = 10000000 / FPS;

HRESULT MakeType(const Size& size, IMFMediaType** out) {
    ComPtr<IMFMediaType> type;
    CHECK(MFCreateMediaType(&type));
    UINT32 bytes = size.width * size.height * 3 / 2;
    CHECK(type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video));
    CHECK(type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12));
    CHECK(MFSetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, size.width, size.height));
    CHECK(MFSetAttributeRatio(type.Get(), MF_MT_FRAME_RATE, FPS, 1));
    CHECK(MFSetAttributeRatio(type.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1));
    CHECK(type->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive));
    CHECK(type->SetUINT32(MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE));
    CHECK(type->SetUINT32(MF_MT_FIXED_SIZE_SAMPLES, TRUE));
    CHECK(type->SetUINT32(MF_MT_DEFAULT_STRIDE, size.width));
    CHECK(type->SetUINT32(MF_MT_SAMPLE_SIZE, bytes));
    CHECK(type->SetUINT32(MF_MT_AVG_BITRATE, bytes * 8 * FPS));
    *out = type.Detach();
    return S_OK;
}

// -- frames written by tessera-camera.exe ------------------------------------

class Frames {
public:
    ~Frames() { Close(); }

    void Open() {
        if (_view) return;
        PSECURITY_DESCRIPTOR sd = nullptr;
        // The signed-in user writes frames; Frame Server reads them.
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:(A;;GA;;;SY)(A;;GA;;;LS)(A;;GA;;;BA)(A;;GRGW;;;IU)", SDDL_REVISION_1, &sd, nullptr);
        SECURITY_ATTRIBUTES sa = {sizeof(sa), sd, FALSE};
        ULONGLONG size = FramesSize();
        for (const wchar_t* name : {FRAMES_GLOBAL, FRAMES_LOCAL}) {
            _section = CreateFileMappingW(INVALID_HANDLE_VALUE, sd ? &sa : nullptr, PAGE_READWRITE,
                                          static_cast<DWORD>(size >> 32), static_cast<DWORD>(size), name);
            if (_section) break;
        }
        if (sd) LocalFree(sd);
        if (_section) _view = static_cast<FrameHeader*>(MapViewOfFile(_section, FILE_MAP_READ, 0, 0, 0));
    }

    void Close() {
        if (_view) UnmapViewOfFile(_view);
        if (_section) CloseHandle(_section);
        _view = nullptr;
        _section = nullptr;
    }

    // Draws the newest frame, scaled, or black before the first one.
    void Paint(BYTE* dest, LONG stride, UINT32 width, UINT32 height) {
        if (!Refresh()) {
            for (UINT32 y = 0; y < height; ++y) memset(dest + y * stride, 16, stride);
            for (UINT32 y = 0; y < height / 2; ++y) memset(dest + (height + y) * stride, 128, stride);
            return;
        }
        _xs.resize(width);
        for (UINT32 x = 0; x < width; ++x) _xs[x] = x * _width / width;
        const BYTE* luma = _last.data();
        for (UINT32 y = 0; y < height; ++y) {
            const BYTE* from = luma + static_cast<size_t>(y * _height / height) * _width;
            BYTE* to = dest + static_cast<size_t>(y) * stride;
            for (UINT32 x = 0; x < width; ++x) to[x] = from[_xs[x]];
        }
        const BYTE* chroma = luma + static_cast<size_t>(_width) * _height;
        for (UINT32 y = 0; y < height / 2; ++y) {
            const BYTE* from = chroma + static_cast<size_t>(y * (_height / 2) / (height / 2)) * _width;
            BYTE* to = dest + static_cast<size_t>(height + y) * stride;
            for (UINT32 x = 0; x < width / 2; ++x) {
                UINT32 sx = (x * (_width / 2) / (width / 2)) * 2;
                to[x * 2] = from[sx];
                to[x * 2 + 1] = from[sx + 1];
            }
        }
    }

private:
    bool Refresh() {
        if (!_view || _view->magic != FRAMES_MAGIC) return !_last.empty();
        for (int attempt = 0; attempt < 3; ++attempt) {
            LONG64 before = _view->sequence;
            if (before == _seen) return !_last.empty();
            if (before & 1) {
                Sleep(1);
                continue;
            }
            UINT32 w = _view->width, h = _view->height;
            if (!w || !h || w > MAX_WIDTH || h > MAX_HEIGHT || (w & 1) || (h & 1)) return !_last.empty();
            size_t bytes = static_cast<size_t>(w) * h * 3 / 2;
            _buffer.resize(bytes);
            memcpy(_buffer.data(), reinterpret_cast<const BYTE*>(_view + 1), bytes);
            if (_view->sequence != before) continue;
            _last.swap(_buffer);
            _width = w;
            _height = h;
            _seen = before;
            return true;
        }
        return !_last.empty();
    }

    HANDLE _section = nullptr;
    FrameHeader* _view = nullptr;
    std::vector<BYTE> _buffer, _last;
    std::vector<UINT32> _xs;
    UINT32 _width = 0, _height = 0;
    LONG64 _seen = -1;
};

// -- the stream --------------------------------------------------------------

class Stream : public RuntimeClass<RuntimeClassFlags<ClassicCom>,
                                   ChainInterfaces<IMFMediaStream2, IMFMediaStream, IMFMediaEventGenerator>,
                                   IKsControl> {
public:
    HRESULT RuntimeClassInitialize(IMFMediaSource* source, DWORD id) {
        _source = source;
        _id = id;
        CHECK(MFCreateEventQueue(&_queue));
        ComPtr<IMFMediaType> types[ARRAYSIZE(SIZES)];
        IMFMediaType* raw[ARRAYSIZE(SIZES)];
        for (size_t i = 0; i < ARRAYSIZE(SIZES); ++i) {
            CHECK(MakeType(SIZES[i], &types[i]));
            raw[i] = types[i].Get();
        }
        CHECK(MFCreateStreamDescriptor(id, ARRAYSIZE(SIZES), raw, &_descriptor));
        ComPtr<IMFMediaTypeHandler> handler;
        CHECK(_descriptor->GetMediaTypeHandler(&handler));
        CHECK(handler->SetCurrentMediaType(raw[0]));
        CHECK(_descriptor->SetGUID(MF_DEVICESTREAM_STREAM_CATEGORY, PINNAME_VIDEO_CAPTURE));
        CHECK(_descriptor->SetUINT32(MF_DEVICESTREAM_STREAM_ID, id));
        CHECK(_descriptor->SetUINT32(MF_DEVICESTREAM_FRAMESERVER_SHARED, 1));
        CHECK(_descriptor->SetUINT32(MF_DEVICESTREAM_ATTRIBUTE_FRAMESOURCE_TYPES, MFFrameSourceTypes_Color));
        return S_OK;
    }

    IMFStreamDescriptor* Descriptor() { return _descriptor.Get(); }

    HRESULT Start() {
        std::lock_guard<std::mutex> lock(_lock);
        if (!_queue) return MF_E_SHUTDOWN;
        _frames.Open();
        if (_allocator) {
            ComPtr<IMFMediaType> type;
            CHECK(CurrentType(&type));
            CHECK(_allocator->InitializeSampleAllocatorEx(2, 10, nullptr, type.Get()));
        }
        _state = MF_STREAM_STATE_RUNNING;
        return _queue->QueueEventParamVar(MEStreamStarted, GUID_NULL, S_OK, nullptr);
    }

    HRESULT Stop() {
        std::lock_guard<std::mutex> lock(_lock);
        if (!_queue) return MF_E_SHUTDOWN;
        _state = MF_STREAM_STATE_STOPPED;
        if (_allocator) _allocator->UninitializeSampleAllocator();
        return _queue->QueueEventParamVar(MEStreamStopped, GUID_NULL, S_OK, nullptr);
    }

    void Shutdown() {
        std::lock_guard<std::mutex> lock(_lock);
        _state = MF_STREAM_STATE_STOPPED;
        if (_queue) _queue->Shutdown();
        _queue.Reset();
        _allocator.Reset();
        _frames.Close();
        _source = nullptr;
    }

    void SetAllocator(IUnknown* allocator) {
        std::lock_guard<std::mutex> lock(_lock);
        _allocator.Reset();
        if (allocator) allocator->QueryInterface(IID_PPV_ARGS(&_allocator));
    }

    // IMFMediaEventGenerator
    STDMETHODIMP GetEvent(DWORD flags, IMFMediaEvent** event) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->GetEvent(flags, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP BeginGetEvent(IMFAsyncCallback* callback, IUnknown* state) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->BeginGetEvent(callback, state) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP EndGetEvent(IMFAsyncResult* result, IMFMediaEvent** event) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->EndGetEvent(result, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP QueueEvent(MediaEventType type, REFGUID extended, HRESULT status, const PROPVARIANT* value) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->QueueEventParamVar(type, extended, status, value) : MF_E_SHUTDOWN;
    }

    // IMFMediaStream
    STDMETHODIMP GetMediaSource(IMFMediaSource** source) override {
        std::lock_guard<std::mutex> lock(_lock);
        if (!source) return E_POINTER;
        if (!_source) return MF_E_SHUTDOWN;
        *source = _source;
        _source->AddRef();
        return S_OK;
    }
    STDMETHODIMP GetStreamDescriptor(IMFStreamDescriptor** descriptor) override {
        return _descriptor.CopyTo(descriptor);
    }
    STDMETHODIMP RequestSample(IUnknown* token) override {
        ComPtr<IMFSample> sample;
        ComPtr<IMFMediaEventQueue> queue;
        {
            std::lock_guard<std::mutex> lock(_lock);
            if (!_queue) return MF_E_SHUTDOWN;
            if (_state != MF_STREAM_STATE_RUNNING) return MF_E_MEDIA_SOURCE_WRONGSTATE;

            ComPtr<IMFMediaType> type;
            CHECK(CurrentType(&type));
            UINT32 width = 0, height = 0;
            CHECK(MFGetAttributeSize(type.Get(), MF_MT_FRAME_SIZE, &width, &height));
            DWORD bytes = width * height * 3 / 2;

            // At most FPS frames a second, however fast they are asked for.
            MFTIME now = MFGetSystemTime();
            if (_next > now) {
                Sleep(static_cast<DWORD>((_next - now) / 10000));
                now = MFGetSystemTime();
            }
            _next = (now > _next + FRAME_TIME ? now : _next) + FRAME_TIME;

            if (_allocator) {
                CHECK(_allocator->AllocateSample(&sample));
            } else {
                ComPtr<IMFMediaBuffer> memory;
                CHECK(MFCreateSample(&sample));
                CHECK(MFCreateMemoryBuffer(bytes, &memory));
                CHECK(memory->SetCurrentLength(bytes));
                CHECK(sample->AddBuffer(memory.Get()));
            }

            ComPtr<IMFMediaBuffer> buffer;
            CHECK(sample->GetBufferByIndex(0, &buffer));
            ComPtr<IMF2DBuffer2> surface;
            if (SUCCEEDED(buffer.As(&surface))) {
                BYTE* scan0 = nullptr;
                BYTE* start = nullptr;
                LONG stride = 0;
                DWORD length = 0;
                CHECK(surface->Lock2DSize(MF2DBuffer_LockFlags_Write, &scan0, &stride, &start, &length));
                _frames.Paint(scan0, stride < 0 ? -stride : stride, width, height);
                surface->Unlock2D();
            } else {
                BYTE* data = nullptr;
                DWORD capacity = 0;
                CHECK(buffer->Lock(&data, &capacity, nullptr));
                if (capacity >= bytes) _frames.Paint(data, static_cast<LONG>(width), width, height);
                buffer->Unlock();
                buffer->SetCurrentLength(bytes);
            }

            CHECK(sample->SetSampleTime(now));
            CHECK(sample->SetSampleDuration(FRAME_TIME));
            if (token) CHECK(sample->SetUnknown(MFSampleExtension_Token, token));
            queue = _queue;
        }
        return queue->QueueEventParamUnk(MEMediaSample, GUID_NULL, S_OK, sample.Get());
    }

    // IMFMediaStream2
    STDMETHODIMP SetStreamState(MF_STREAM_STATE value) override {
        if (value == MF_STREAM_STATE_RUNNING) return Start();
        if (value == MF_STREAM_STATE_STOPPED) return Stop();
        std::lock_guard<std::mutex> lock(_lock);
        if (_state != MF_STREAM_STATE_RUNNING) return MF_E_INVALID_STATE_TRANSITION;
        _state = MF_STREAM_STATE_PAUSED;
        return S_OK;
    }
    STDMETHODIMP GetStreamState(MF_STREAM_STATE* value) override {
        if (!value) return E_POINTER;
        std::lock_guard<std::mutex> lock(_lock);
        *value = _state;
        return S_OK;
    }

    // IKsControl: no camera controls.
    STDMETHODIMP KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }
    STDMETHODIMP KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }
    STDMETHODIMP KsEvent(PKSEVENT, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }

private:
    ComPtr<IMFMediaEventQueue> Queue() {
        std::lock_guard<std::mutex> lock(_lock);
        return _queue;
    }

    HRESULT CurrentType(IMFMediaType** type) {
        ComPtr<IMFMediaTypeHandler> handler;
        CHECK(_descriptor->GetMediaTypeHandler(&handler));
        return handler->GetCurrentMediaType(type);
    }

    std::mutex _lock;
    IMFMediaSource* _source = nullptr;  // not owned: the source owns this stream
    DWORD _id = 0;
    MF_STREAM_STATE _state = MF_STREAM_STATE_STOPPED;
    MFTIME _next = 0;
    ComPtr<IMFMediaEventQueue> _queue;
    ComPtr<IMFStreamDescriptor> _descriptor;
    ComPtr<IMFVideoSampleAllocatorEx> _allocator;
    Frames _frames;
};

// -- the source --------------------------------------------------------------

class Source : public RuntimeClass<RuntimeClassFlags<ClassicCom>,
                                   ChainInterfaces<IMFMediaSource2, IMFMediaSourceEx, IMFMediaSource,
                                                   IMFMediaEventGenerator>,
                                   IMFGetService, IKsControl, IMFSampleAllocatorControl> {
public:
    HRESULT RuntimeClassInitialize() {
        CHECK(MFCreateEventQueue(&_queue));
        CHECK(MFCreateAttributes(&_attributes, 2));
        CHECK(MakeAndInitialize<Stream>(&_stream, static_cast<IMFMediaSource*>(this), 0));
        IMFStreamDescriptor* descriptor = _stream->Descriptor();
        CHECK(MFCreatePresentationDescriptor(1, &descriptor, &_presentation));
        CHECK(_presentation->SelectStream(0));
        AddProfiles();
        return S_OK;
    }

    // IMFMediaEventGenerator
    STDMETHODIMP GetEvent(DWORD flags, IMFMediaEvent** event) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->GetEvent(flags, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP BeginGetEvent(IMFAsyncCallback* callback, IUnknown* state) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->BeginGetEvent(callback, state) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP EndGetEvent(IMFAsyncResult* result, IMFMediaEvent** event) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->EndGetEvent(result, event) : MF_E_SHUTDOWN;
    }
    STDMETHODIMP QueueEvent(MediaEventType type, REFGUID extended, HRESULT status, const PROPVARIANT* value) override {
        ComPtr<IMFMediaEventQueue> queue = Queue();
        return queue ? queue->QueueEventParamVar(type, extended, status, value) : MF_E_SHUTDOWN;
    }

    // IMFMediaSource
    STDMETHODIMP GetCharacteristics(DWORD* characteristics) override {
        if (!characteristics) return E_POINTER;
        *characteristics = MFMEDIASOURCE_IS_LIVE;
        return S_OK;
    }
    STDMETHODIMP CreatePresentationDescriptor(IMFPresentationDescriptor** descriptor) override {
        std::lock_guard<std::mutex> lock(_lock);
        if (!descriptor) return E_POINTER;
        if (!_presentation) return MF_E_SHUTDOWN;
        return _presentation->Clone(descriptor);
    }
    STDMETHODIMP Start(IMFPresentationDescriptor* descriptor, const GUID* format, const PROPVARIANT*) override {
        if (!descriptor) return E_POINTER;
        if (format && *format != GUID_NULL) return MF_E_UNSUPPORTED_TIME_FORMAT;
        std::lock_guard<std::mutex> lock(_lock);
        if (!_queue) return MF_E_SHUTDOWN;

        BOOL selected = FALSE;
        ComPtr<IMFStreamDescriptor> stream;
        CHECK(descriptor->GetStreamDescriptorByIndex(0, &selected, &stream));
        if (selected) {
            CHECK(_presentation->SelectStream(0));
            CHECK(_queue->QueueEventParamUnk(_started ? MEUpdatedStream : MENewStream, GUID_NULL, S_OK,
                                             static_cast<IMFMediaStream*>(_stream.Get())));
            CHECK(_stream->Start());
            _started = true;
        } else {
            CHECK(_presentation->DeselectStream(0));
            _stream->Stop();
        }

        PROPVARIANT time;
        PropVariantInit(&time);
        time.vt = VT_I8;
        time.hVal.QuadPart = MFGetSystemTime();
        return _queue->QueueEventParamVar(MESourceStarted, GUID_NULL, S_OK, &time);
    }
    STDMETHODIMP Stop() override {
        std::lock_guard<std::mutex> lock(_lock);
        if (!_queue) return MF_E_SHUTDOWN;
        _stream->Stop();
        PROPVARIANT time;
        PropVariantInit(&time);
        time.vt = VT_I8;
        time.hVal.QuadPart = MFGetSystemTime();
        return _queue->QueueEventParamVar(MESourceStopped, GUID_NULL, S_OK, &time);
    }
    STDMETHODIMP Pause() override { return MF_E_INVALID_STATE_TRANSITION; }
    STDMETHODIMP Shutdown() override {
        std::lock_guard<std::mutex> lock(_lock);
        if (_stream) _stream->Shutdown();
        if (_queue) _queue->Shutdown();
        _stream.Reset();
        _queue.Reset();
        _presentation.Reset();
        return S_OK;
    }

    // IMFMediaSourceEx
    STDMETHODIMP GetSourceAttributes(IMFAttributes** attributes) override {
        return _attributes.CopyTo(attributes);
    }
    STDMETHODIMP GetStreamAttributes(DWORD id, IMFAttributes** attributes) override {
        if (!attributes) return E_POINTER;
        if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
        std::lock_guard<std::mutex> lock(_lock);
        if (!_stream) return MF_E_SHUTDOWN;
        return _stream->Descriptor()->QueryInterface(IID_PPV_ARGS(attributes));
    }
    STDMETHODIMP SetD3DManager(IUnknown*) override { return S_OK; }

    // IMFMediaSource2
    STDMETHODIMP SetMediaType(DWORD id, IMFMediaType* type) override {
        if (!type) return E_POINTER;
        if (id != 0) return MF_E_INVALIDSTREAMNUMBER;
        std::lock_guard<std::mutex> lock(_lock);
        if (!_stream) return MF_E_SHUTDOWN;
        ComPtr<IMFMediaTypeHandler> handler;
        CHECK(_stream->Descriptor()->GetMediaTypeHandler(&handler));
        return handler->SetCurrentMediaType(type);
    }

    // IMFGetService
    STDMETHODIMP GetService(REFGUID, REFIID, LPVOID*) override { return MF_E_UNSUPPORTED_SERVICE; }

    // IKsControl
    STDMETHODIMP KsProperty(PKSPROPERTY, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }
    STDMETHODIMP KsMethod(PKSMETHOD, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }
    STDMETHODIMP KsEvent(PKSEVENT, ULONG, LPVOID, ULONG, ULONG*) override {
        return HRESULT_FROM_WIN32(ERROR_SET_NOT_FOUND);
    }

    // IMFSampleAllocatorControl
    STDMETHODIMP SetDefaultAllocator(DWORD output, IUnknown* allocator) override {
        if (output != 0) return MF_E_INVALIDSTREAMNUMBER;
        std::lock_guard<std::mutex> lock(_lock);
        if (!_stream) return MF_E_SHUTDOWN;
        _stream->SetAllocator(allocator);
        return S_OK;
    }
    STDMETHODIMP GetAllocatorUsage(DWORD output, DWORD* input, MFSampleAllocatorUsage* usage) override {
        if (!input || !usage) return E_POINTER;
        if (output != 0) return MF_E_INVALIDSTREAMNUMBER;
        *input = output;
        *usage = MFSampleAllocatorUsage_UsesProvidedAllocator;
        return S_OK;
    }

private:
    ComPtr<IMFMediaEventQueue> Queue() {
        std::lock_guard<std::mutex> lock(_lock);
        return _queue;
    }

    void AddProfiles() {
        ComPtr<IMFSensorProfileCollection> collection;
        ComPtr<IMFSensorProfile> profile;
        if (FAILED(MFCreateSensorProfileCollection(&collection))) return;
        if (SUCCEEDED(MFCreateSensorProfile(KSCAMERAPROFILE_Legacy, 0, nullptr, &profile))) {
            profile->AddProfileFilter(0, L"((RES==;FRT<=30,1;SUT==))");
            collection->AddProfile(profile.Get());
        }
        _attributes->SetUnknown(MF_DEVICEMFT_SENSORPROFILE_COLLECTION, collection.Get());
    }

    std::mutex _lock;
    bool _started = false;
    ComPtr<IMFMediaEventQueue> _queue;
    ComPtr<IMFAttributes> _attributes;
    ComPtr<IMFPresentationDescriptor> _presentation;
    ComPtr<Stream> _stream;
};

// -- the activator Frame Server creates --------------------------------------

class Activator : public RuntimeClass<RuntimeClassFlags<ClassicCom>, ChainInterfaces<IMFActivate, IMFAttributes>> {
public:
    HRESULT RuntimeClassInitialize() {
        CHECK(MFCreateAttributes(&_store, 2));
        CHECK(_store->SetUINT32(MF_VIRTUALCAMERA_PROVIDE_ASSOCIATED_CAMERA_SOURCES, 1));
        CHECK(_store->SetGUID(MFT_TRANSFORM_CLSID_Attribute, CLSID_TesseraCamera));
        return S_OK;
    }

    // IMFActivate
    STDMETHODIMP ActivateObject(REFIID riid, void** object) override {
        if (!object) return E_POINTER;
        std::lock_guard<std::mutex> lock(_lock);
        if (!_source) CHECK(MakeAndInitialize<Source>(&_source));
        return _source.CopyTo(riid, object);
    }
    STDMETHODIMP ShutdownObject() override {
        std::lock_guard<std::mutex> lock(_lock);
        if (_source) _source->Shutdown();
        return S_OK;
    }
    STDMETHODIMP DetachObject() override {
        std::lock_guard<std::mutex> lock(_lock);
        _source.Reset();
        return S_OK;
    }

    // IMFAttributes, kept in _store
    STDMETHODIMP GetItem(REFGUID k, PROPVARIANT* v) override { return _store->GetItem(k, v); }
    STDMETHODIMP GetItemType(REFGUID k, MF_ATTRIBUTE_TYPE* t) override { return _store->GetItemType(k, t); }
    STDMETHODIMP CompareItem(REFGUID k, REFPROPVARIANT v, BOOL* r) override { return _store->CompareItem(k, v, r); }
    STDMETHODIMP Compare(IMFAttributes* a, MF_ATTRIBUTES_MATCH_TYPE m, BOOL* r) override { return _store->Compare(a, m, r); }
    STDMETHODIMP GetUINT32(REFGUID k, UINT32* v) override { return _store->GetUINT32(k, v); }
    STDMETHODIMP GetUINT64(REFGUID k, UINT64* v) override { return _store->GetUINT64(k, v); }
    STDMETHODIMP GetDouble(REFGUID k, double* v) override { return _store->GetDouble(k, v); }
    STDMETHODIMP GetGUID(REFGUID k, GUID* v) override { return _store->GetGUID(k, v); }
    STDMETHODIMP GetStringLength(REFGUID k, UINT32* n) override { return _store->GetStringLength(k, n); }
    STDMETHODIMP GetString(REFGUID k, LPWSTR s, UINT32 n, UINT32* l) override { return _store->GetString(k, s, n, l); }
    STDMETHODIMP GetAllocatedString(REFGUID k, LPWSTR* s, UINT32* l) override { return _store->GetAllocatedString(k, s, l); }
    STDMETHODIMP GetBlobSize(REFGUID k, UINT32* n) override { return _store->GetBlobSize(k, n); }
    STDMETHODIMP GetBlob(REFGUID k, UINT8* b, UINT32 n, UINT32* l) override { return _store->GetBlob(k, b, n, l); }
    STDMETHODIMP GetAllocatedBlob(REFGUID k, UINT8** b, UINT32* n) override { return _store->GetAllocatedBlob(k, b, n); }
    STDMETHODIMP GetUnknown(REFGUID k, REFIID i, LPVOID* p) override { return _store->GetUnknown(k, i, p); }
    STDMETHODIMP SetItem(REFGUID k, REFPROPVARIANT v) override { return _store->SetItem(k, v); }
    STDMETHODIMP DeleteItem(REFGUID k) override { return _store->DeleteItem(k); }
    STDMETHODIMP DeleteAllItems() override { return _store->DeleteAllItems(); }
    STDMETHODIMP SetUINT32(REFGUID k, UINT32 v) override { return _store->SetUINT32(k, v); }
    STDMETHODIMP SetUINT64(REFGUID k, UINT64 v) override { return _store->SetUINT64(k, v); }
    STDMETHODIMP SetDouble(REFGUID k, double v) override { return _store->SetDouble(k, v); }
    STDMETHODIMP SetGUID(REFGUID k, REFGUID v) override { return _store->SetGUID(k, v); }
    STDMETHODIMP SetString(REFGUID k, LPCWSTR v) override { return _store->SetString(k, v); }
    STDMETHODIMP SetBlob(REFGUID k, const UINT8* b, UINT32 n) override { return _store->SetBlob(k, b, n); }
    STDMETHODIMP SetUnknown(REFGUID k, IUnknown* u) override { return _store->SetUnknown(k, u); }
    STDMETHODIMP LockStore() override { return _store->LockStore(); }
    STDMETHODIMP UnlockStore() override { return _store->UnlockStore(); }
    STDMETHODIMP GetCount(UINT32* n) override { return _store->GetCount(n); }
    STDMETHODIMP GetItemByIndex(UINT32 i, GUID* k, PROPVARIANT* v) override { return _store->GetItemByIndex(i, k, v); }
    STDMETHODIMP CopyAllItems(IMFAttributes* d) override { return _store->CopyAllItems(d); }

private:
    std::mutex _lock;
    ComPtr<IMFAttributes> _store;
    ComPtr<Source> _source;
};

class Factory : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IClassFactory> {
public:
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** object) override {
        if (!object) return E_POINTER;
        *object = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        ComPtr<Activator> activator;
        CHECK(MakeAndInitialize<Activator>(&activator));
        return activator.CopyTo(riid, object);
    }
    STDMETHODIMP LockServer(BOOL) override { return S_OK; }
};

std::wstring KeyPath() {
    return std::wstring(L"Software\\Classes\\CLSID\\") + TESSERA_CAMERA_CLSID + L"\\InprocServer32";
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, LPVOID* object) {
    if (!object) return E_POINTER;
    *object = nullptr;
    if (clsid != CLSID_TesseraCamera) return CLASS_E_CLASSNOTAVAILABLE;
    ComPtr<Factory> factory = Microsoft::WRL::Make<Factory>();
    if (!factory) return E_OUTOFMEMORY;
    return factory.CopyTo(riid, object);
}

STDAPI DllCanUnloadNow() { return S_FALSE; }

// HKLM, as Frame Server only looks there: needs an administrator.
STDAPI DllRegisterServer() {
    wchar_t path[MAX_PATH];
    if (!GetModuleFileNameW(g_module, path, MAX_PATH)) return HRESULT_FROM_WIN32(GetLastError());
    HKEY key;
    LSTATUS status = RegCreateKeyExW(HKEY_LOCAL_MACHINE, KeyPath().c_str(), 0, nullptr, 0, KEY_WRITE, nullptr, &key, nullptr);
    if (status != ERROR_SUCCESS) return HRESULT_FROM_WIN32(status);
    status = RegSetValueExW(key, nullptr, 0, REG_SZ, reinterpret_cast<const BYTE*>(path),
                            static_cast<DWORD>((wcslen(path) + 1) * sizeof(wchar_t)));
    if (status == ERROR_SUCCESS) {
        static const wchar_t both[] = L"Both";
        status = RegSetValueExW(key, L"ThreadingModel", 0, REG_SZ, reinterpret_cast<const BYTE*>(both), sizeof(both));
    }
    RegCloseKey(key);
    return HRESULT_FROM_WIN32(status);
}

STDAPI DllUnregisterServer() {
    std::wstring path = std::wstring(L"Software\\Classes\\CLSID\\") + TESSERA_CAMERA_CLSID;
    LSTATUS status = RegDeleteTreeW(HKEY_LOCAL_MACHINE, path.c_str());
    return status == ERROR_FILE_NOT_FOUND ? S_OK : HRESULT_FROM_WIN32(status);
}
