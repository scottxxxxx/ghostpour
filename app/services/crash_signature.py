"""Crash signature for MetricKit crash and hang reports.

A signature groups reports that are the same bug. It is two parts joined by
a pipe:

  <code>|<frame>

`code` is the first non-empty of exception_code, signal, exception_type
(the order SS and GP agreed), taken from the top-level request fields and
falling back to the same keys inside the payload's diagnosticMetaData. A
report with none of them (a hang, usually) uses its kind.

`frame` is the top APP frame of the attributed thread: in the payload's
callStackTree, the thread with threadAttributed=true (else the first
thread), walked from callStackRootFrames down through subFrames to the
first frame whose binaryName is not a system library. It is written as
binaryName+offsetIntoBinaryTextSegment, because the offset is stable for a
given build where the runtime address is not (ASLR). When no frame is an
app frame, the topmost frame of that thread is used with a "sys:" prefix,
and when the shape is not a call stack tree at all the frame is
"no_stack". The function never raises on odd input: a report with an
unexpected shape is still worth storing.

Apple's documented MXCallStackTree JSON shape:

  {"callStackPerThread": true,
   "callStacks": [
     {"threadAttributed": true,
      "callStackRootFrames": [
        {"binaryUUID": "...", "offsetIntoBinaryTextSegment": 123,
         "sampleCount": 1, "binaryName": "MyApp", "address": 74565,
         "subFrames": [ {...same shape...} ]}
      ]}
   ]}

In a crash the first root frame is the innermost frame (where the program
counter was) and subFrames lead outward to its callers, so a depth-first
walk meets the frames in top-of-stack order.
"""

from __future__ import annotations

from typing import Any

# Binaries that are part of iOS rather than the app. Exact names, then
# prefixes. Everything not matched counts as the app's own code, which is
# the right default: an embedded third party framework the app ships is
# still the app's crash to fix.
_SYSTEM_EXACT = frozenset({
    "dyld", "Foundation", "CoreFoundation", "UIKitCore", "UIKit", "SwiftUI",
    "SwiftUICore", "Swift", "GraphicsServices", "QuartzCore", "CoreGraphics",
    "Combine", "Security", "Network", "Metal", "MetalKit", "Accelerate",
    "Speech", "MediaToolbox", "AudioToolbox", "CoreAudio", "AVFoundation",
    "AVFAudio", "AVFCore", "CoreMedia", "CoreML", "Vision", "CoreData",
    "CloudKit", "StoreKit", "WebKit", "JavaScriptCore", "FrontBoardServices",
    "BaseBoard", "RunningBoardServices", "BackBoardServices", "CFNetwork",
    "IOKit", "ImageIO", "CoreText", "CoreAnimation", "CoreLocation",
    "AuthenticationServices", "UserNotifications", "MetricKit", "Observation",
    "SwiftData", "Darwin", "_Concurrency", "libswift_Concurrency",
    "AppleAccount", "Contacts", "EventKit", "Photos", "PhotosUI", "ARKit",
    "SceneKit", "SpriteKit", "GameKit", "MapKit", "Intents", "IntentsUI",
    "AppIntents", "WidgetKit", "ActivityKit", "BackgroundTasks",
    "OSLog", "os", "Dispatch", "ObjectiveC", "CoreServices",
    "MobileCoreServices", "SystemConfiguration", "LocalAuthentication",
    "CoreHaptics", "CoreMotion", "CoreBluetooth", "CallKit", "PushKit",
})
_SYSTEM_PREFIXES = ("lib", "dyld", "Core", "UIKit", "AV", "Swift_")

_CODE_KEYS = ("exception_code", "signal", "exception_type")
# MXCrashDiagnostic.jsonRepresentation spells these in camelCase inside
# diagnosticMetaData.
_META_KEYS = {"exception_code": "exceptionCode", "signal": "signal",
              "exception_type": "exceptionType"}


def is_system_binary(name: Any) -> bool:
    if not isinstance(name, str) or not name:
        return True  # an unnamed frame cannot be attributed to the app
    return name in _SYSTEM_EXACT or name.startswith(_SYSTEM_PREFIXES)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _code_part(fields: dict, payload: Any, kind: str) -> str:
    for key in _CODE_KEYS:
        value = _clean(fields.get(key))
        if value:
            return value
    meta = payload.get("diagnosticMetaData") if isinstance(payload, dict) else None
    if isinstance(meta, dict):
        for key in _CODE_KEYS:
            value = _clean(meta.get(_META_KEYS[key]))
            if value:
                return value
    return kind or "unknown"


def _call_stack_tree(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None
    tree = payload.get("callStackTree")
    if isinstance(tree, dict):
        return tree
    # The tree itself was sent as the payload.
    if isinstance(payload.get("callStacks"), list):
        return payload
    return None


def _pick_thread(tree: dict) -> dict | None:
    stacks = tree.get("callStacks")
    if not isinstance(stacks, list):
        return None
    threads = [t for t in stacks if isinstance(t, dict)]
    for thread in threads:
        if thread.get("threadAttributed") is True:
            return thread
    return threads[0] if threads else None


def _walk(frames: Any, limit: int = 4096):
    """Depth-first over root frames and their subFrames, top of stack
    first. Iterative with a node budget, so a hostile or cyclic-looking
    payload cannot blow the recursion limit or spin."""
    if not isinstance(frames, list):
        return
    stack = list(reversed(frames))
    seen = 0
    while stack and seen < limit:
        frame = stack.pop()
        if not isinstance(frame, dict):
            continue
        seen += 1
        yield frame
        subs = frame.get("subFrames")
        if isinstance(subs, list):
            stack.extend(reversed(subs))


def _frame_label(frame: dict) -> str:
    name = _clean(frame.get("binaryName")) or "?"
    offset = frame.get("offsetIntoBinaryTextSegment")
    if offset is None:
        offset = frame.get("address")
    return f"{name}+{offset}" if offset is not None else name


def _frame_part(payload: Any) -> str:
    tree = _call_stack_tree(payload)
    if tree is None:
        return "no_stack"
    thread = _pick_thread(tree)
    if thread is None:
        return "no_stack"
    first = None
    for frame in _walk(thread.get("callStackRootFrames")):
        if first is None:
            first = frame
        if not is_system_binary(frame.get("binaryName")):
            return _frame_label(frame)
    if first is not None:
        return "sys:" + _frame_label(first)
    return "no_stack"


def derive_signature(fields: dict, payload: Any) -> str:
    """`fields` holds the top-level request scalars (exception_code, signal,
    exception_type, kind); `payload` is the parsed MetricKit payload."""
    try:
        code = _code_part(fields, payload, _clean(fields.get("kind")) or "unknown")
        frame = _frame_part(payload)
    except Exception:  # noqa: BLE001, a signature must never fail an intake
        return "unknown|no_stack"
    return f"{code}|{frame}"[:512]
