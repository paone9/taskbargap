"""Read the Windows 11 taskbar's own layout through UI Automation, in plain ctypes.

Stock Windows 11 draws its taskbar in XAML. The legacy button windows the Win32
path measures (``ReBarWindow32``, ``MSTaskListWClass``) still exist there, but
their rects stopped tracking the buttons actually drawn, which is how
``find_gap()`` came to place a "measured" left edge on top of real icons
(CHANGELOG 0.1.1). The one source that does describe the drawn taskbar is the
accessibility tree Explorer publishes for Narrator: one UI Automation element
per app button, search box, Task View and Widgets toggle, each with its screen
rectangle.

This module reads that tree with nothing beyond ctypes, driving the
``CUIAutomation`` COM object through its raw vtables. It is read-only and asks
for rectangles only: no names, no window text. Every failure returns None so
detection falls back to the legacy windows, and nothing here runs at import
time, so the package still imports anywhere.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time

log = logging.getLogger("taskbargap")

#: The XAML taskbar's frame. Its direct children are exactly the things a window
#: must not cover: Start, Search, Task View, every app button, Widgets, Copilot.
#: The tray icons and clock live elsewhere in the tree (``TrayNotifyWnd`` still
#: bounds them, see ``_detect``). Windows 10 and Server have no such element.
TASKBAR_FRAME_CLASS = "Taskbar.TaskbarFrameAutomationPeer"

_CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
_IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"
_CLSCTX_INPROC_SERVER = 1
_TREE_SCOPE_CHILDREN = 2
_TREE_SCOPE_DESCENDANTS = 4
_UIA_BOUNDING_RECTANGLE = 30001
_UIA_CLASS_NAME = 30012
_VT_BSTR = 8
_VT_ARRAY_R8 = 0x2000 | 5
_S_OK = 0

# vtable slots, counted from IUnknown's three. Validated live against the
# .NET UIAutomationClient wrapper on Windows 11 26200 (identical rectangles).
_IUIA_ELEMENT_FROM_HANDLE = 6
_IUIA_CREATE_CACHE_REQUEST = 20
_IUIA_CREATE_TRUE_CONDITION = 21
_IUIA_CREATE_PROPERTY_CONDITION = 23
_IEL_FIND_FIRST = 5
_IEL_FIND_ALL_BUILD_CACHE = 8
_IEL_GET_CACHED_PROPERTY_VALUE = 12
_IARR_GET_LENGTH = 3
_IARR_GET_ELEMENT = 4
_ICACHE_ADD_PROPERTY = 3
_IUNKNOWN_RELEASE = 2

#: After failing to even create the automation object (no UIAutomationCore, a
#: locked-down session) don't retry on every tick: it is not coming back soon.
_RETRY_AFTER_S = 60.0

_tls = threading.local()


class _GUID(ctypes.Structure):
    _fields_ = (("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8))


class _VariantUnion(ctypes.Union):
    _fields_ = (("bstr", ctypes.c_void_p), ("parray", ctypes.c_void_p),
                ("ll", ctypes.c_longlong))


class _VARIANT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("u", _VariantUnion))


class _Com:
    """One thread's COM state: the libraries, the automation object, and the
    prototypes for the handful of vtable slots used. COM objects belong to the
    apartment of the thread that made them, hence thread-local."""

    def __init__(self):
        self.ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        self.oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)
        ole, aut = self.ole32, self.oleaut32
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        ole.CoInitializeEx.restype = ctypes.c_long
        ole.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_GUID)]
        ole.CLSIDFromString.restype = ctypes.c_long
        ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole.CoCreateInstance.restype = ctypes.c_long
        aut.SysAllocString.argtypes = [ctypes.c_wchar_p]
        aut.SysAllocString.restype = ctypes.c_void_p
        aut.VariantClear.argtypes = [ctypes.POINTER(_VARIANT)]
        aut.VariantClear.restype = ctypes.c_long
        aut.SafeArrayAccessData.argtypes = [ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.POINTER(ctypes.c_double))]
        aut.SafeArrayAccessData.restype = ctypes.c_long
        aut.SafeArrayUnaccessData.argtypes = [ctypes.c_void_p]
        aut.SafeArrayUnaccessData.restype = ctypes.c_long
        aut.SafeArrayGetDim.argtypes = [ctypes.c_void_p]
        aut.SafeArrayGetDim.restype = ctypes.c_uint
        aut.SafeArrayGetUBound.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.POINTER(ctypes.c_long)]
        aut.SafeArrayGetUBound.restype = ctypes.c_long
        # Multithreaded apartment. S_FALSE (already initialised) and
        # RPC_E_CHANGED_MODE (the host made this thread STA) both leave COM
        # usable for an in-proc client talking to Explorer, so neither is an
        # error here. Never CoUninitialize: the thread, and its COM state, may
        # belong to the host application.
        ole.CoInitializeEx(None, 0)
        self._protos: dict = {}
        self.uia = ctypes.c_void_p()
        clsid, iid = _GUID(), _GUID()
        ole.CLSIDFromString(_CLSID_CUIAutomation, ctypes.byref(clsid))
        ole.CLSIDFromString(_IID_IUIAutomation, ctypes.byref(iid))
        hr = ole.CoCreateInstance(ctypes.byref(clsid), None, _CLSCTX_INPROC_SERVER,
                                  ctypes.byref(iid), ctypes.byref(self.uia))
        if hr != _S_OK or not self.uia:
            raise OSError(f"CoCreateInstance(CUIAutomation) failed: 0x{hr & 0xFFFFFFFF:08x}")

    def call(self, obj, slot, *args, argtypes=()):
        """Invoke vtable slot ``slot`` on COM pointer ``obj``; returns the HRESULT."""
        key = (slot, argtypes)
        proto = self._protos.get(key)
        if proto is None:
            proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
            self._protos[key] = proto
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
        fn = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[slot]
        return proto(fn)(obj, *args)

    def release(self, obj):
        if obj:
            self.call(obj, _IUNKNOWN_RELEASE)


def _com() -> _Com | None:
    """This thread's COM state, created on first use; None while UI Automation
    is known to be unavailable on this thread (retried after ``_RETRY_AFTER_S``)."""
    com = getattr(_tls, "com", None)
    if com is not None:
        return com
    if time.monotonic() < getattr(_tls, "retry_at", 0.0):
        return None
    try:
        com = _Com()
    except (OSError, AttributeError) as exc:
        log.debug("taskbargap: UI Automation unavailable on this thread: %s", exc)
        _tls.retry_at = time.monotonic() + _RETRY_AFTER_S
        return None
    _tls.com = com
    return com


def _check(hr: int, what: str) -> None:
    if hr != _S_OK:
        raise OSError(f"{what} failed: 0x{hr & 0xFFFFFFFF:08x}")


def _cached_rect(com: _Com, element) -> tuple[int, int, int, int] | None:
    """The element's cached BoundingRectangle as ``(left, top, right, bottom)``
    physical px, or None when it has none (off screen, collapsed)."""
    aut = com.oleaut32
    var = _VARIANT()
    _check(com.call(element, _IEL_GET_CACHED_PROPERTY_VALUE, _UIA_BOUNDING_RECTANGLE,
                    ctypes.byref(var), argtypes=(ctypes.c_int, ctypes.POINTER(_VARIANT))),
           "GetCachedPropertyValue(BoundingRectangle)")
    try:
        if var.vt != _VT_ARRAY_R8 or not var.parray:
            return None
        upper = ctypes.c_long()
        if (aut.SafeArrayGetDim(var.parray) != 1
                or aut.SafeArrayGetUBound(var.parray, 1, ctypes.byref(upper)) != _S_OK
                or upper.value < 3):
            return None
        data = ctypes.POINTER(ctypes.c_double)()
        _check(aut.SafeArrayAccessData(var.parray, ctypes.byref(data)), "SafeArrayAccessData")
        try:
            left, top, width, height = (data[i] for i in range(4))
        finally:
            aut.SafeArrayUnaccessData(var.parray)
        if width <= 0 or height <= 0:
            return None
        return round(left), round(top), round(left + width), round(top + height)
    finally:
        aut.VariantClear(ctypes.byref(var))


def _frame_children(com: _Com, tray: int) -> list[tuple[int, int, int, int]] | None:
    """Rects of the XAML taskbar frame's direct children, or None if the shell
    hosts no such frame (Windows 10, Server, a replaced shell)."""
    aut = com.oleaut32
    root, frame, cond, true_cond, cache, array = (ctypes.c_void_p() for _ in range(6))
    try:
        _check(com.call(com.uia, _IUIA_ELEMENT_FROM_HANDLE, ctypes.c_void_p(tray),
                        ctypes.byref(root),
                        argtypes=(ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))),
               "ElementFromHandle")
        var = _VARIANT()
        var.vt = _VT_BSTR
        var.bstr = aut.SysAllocString(TASKBAR_FRAME_CLASS)
        try:
            _check(com.call(com.uia, _IUIA_CREATE_PROPERTY_CONDITION, _UIA_CLASS_NAME, var,
                            ctypes.byref(cond),
                            argtypes=(ctypes.c_int, _VARIANT, ctypes.POINTER(ctypes.c_void_p))),
                   "CreatePropertyCondition")
        finally:
            aut.VariantClear(ctypes.byref(var))
        _check(com.call(root, _IEL_FIND_FIRST, _TREE_SCOPE_DESCENDANTS, cond, ctypes.byref(frame),
                        argtypes=(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))),
               "FindFirst(TaskbarFrame)")
        if not frame:
            return None                      # not a XAML taskbar
        _check(com.call(com.uia, _IUIA_CREATE_TRUE_CONDITION, ctypes.byref(true_cond),
                        argtypes=(ctypes.POINTER(ctypes.c_void_p),)), "CreateTrueCondition")
        _check(com.call(com.uia, _IUIA_CREATE_CACHE_REQUEST, ctypes.byref(cache),
                        argtypes=(ctypes.POINTER(ctypes.c_void_p),)), "CreateCacheRequest")
        _check(com.call(cache, _ICACHE_ADD_PROPERTY, _UIA_BOUNDING_RECTANGLE,
                        argtypes=(ctypes.c_int,)), "CacheRequest.AddProperty")
        # One cross-process round trip for every child and its rectangle,
        # instead of one per child: ~20ms for a 20-button taskbar.
        _check(com.call(frame, _IEL_FIND_ALL_BUILD_CACHE, _TREE_SCOPE_CHILDREN, true_cond, cache,
                        ctypes.byref(array),
                        argtypes=(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_void_p))),
               "FindAllBuildCache(children)")
        count = ctypes.c_int()
        _check(com.call(array, _IARR_GET_LENGTH, ctypes.byref(count),
                        argtypes=(ctypes.POINTER(ctypes.c_int),)), "ElementArray.Length")
        rects = []
        for i in range(count.value):
            element = ctypes.c_void_p()
            _check(com.call(array, _IARR_GET_ELEMENT, i, ctypes.byref(element),
                            argtypes=(ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))),
                   "ElementArray.GetElement")
            try:
                rect = _cached_rect(com, element)
            finally:
                com.release(element)
            if rect is not None:
                rects.append(rect)
        return rects or None             # an empty frame is Explorer mid-rebuild
    finally:
        for obj in (array, cache, true_cond, frame, cond, root):
            com.release(obj)


def taskbar_items(tray: int) -> list[tuple[int, int, int, int]] | None:
    """Screen rects, physical px, of everything the Windows 11 taskbar draws in
    its button area: Start, Search, Task View, each app button, Widgets.

    None means "no answer", never "nothing there": UI Automation unavailable,
    the shell hosts no XAML taskbar frame (Windows 10, Server, a replacement
    shell), or Explorer didn't respond (mid-restart). Callers fall back to the
    legacy button windows in that case. A frame with no items at all (Explorer
    rebuilding its taskbar) is reported as None too, never as an empty bar.

    Safe to call from any thread; each thread keeps its own COM state.
    """
    if not tray or not sys.platform.startswith("win"):
        return None
    com = _com()
    if com is None:
        return None
    try:
        return _frame_children(com, tray)
    except (OSError, ValueError) as exc:
        log.debug("taskbargap: UI Automation read of the taskbar failed: %s", exc)
        return None
