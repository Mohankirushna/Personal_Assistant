"""Shared macOS Location Services lookup, used by the morning briefing for
accurate current-location weather.

Underscore-prefixed so the registry's discovery walk skips this module.

IP-based geolocation (what a bare weather API falls back to) is routinely
wrong by tens of kilometres on mobile-carrier connections — it resolves to
the carrier's gateway, not the device. CoreLocation's WiFi-based positioning
is far more accurate, but — like Calendar and Contacts elsewhere in this
codebase — macOS only shows the permission prompt to a properly bundled app
that declares NSLocationWhenInUseUsageDescription; a bare `python3`/`uv run`
process is auto-denied with no prompt at all.

Every failure mode (permission denied, no fix within the timeout, CoreLocation
unavailable) returns None rather than raising: callers use this to upgrade an
already-working fallback (configured location / IP geolocation), never as a
hard requirement.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

_LOCATION_TIMEOUT_SECONDS = 8.0
_GEOCODE_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 0.1


def _pump_run_loop_until(done: threading.Event, timeout: float) -> None:
    """Service the current thread's run loop (so CoreLocation's delegate/
    completion callbacks fire) in short slices, stopping as soon as `done` is
    set or `timeout` elapses.

    Deliberately NOT PyObjCTools.AppHelper.runConsoleEventLoop(): it blocks on
    a single runMode:beforeDate: call with no natural wake-up point when
    nothing is scheduled (e.g. a permission request that will never be
    answered because there's no bundled app to show the prompt), so
    stopEventLoop() from a watchdog thread can silently fail to interrupt it.
    Polling in bounded slices makes the timeout actually bounded.
    """
    from Foundation import NSDate, NSRunLoop

    run_loop = NSRunLoop.currentRunLoop()
    deadline = time.monotonic() + timeout
    while not done.is_set() and time.monotonic() < deadline:
        run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(_POLL_INTERVAL_SECONDS))


def _fetch_coordinate(timeout: float) -> tuple[float, float] | None:
    """Blocking: request the device's current coordinate via CoreLocation.
    Must run off the event loop thread (asyncio.to_thread) — it pumps its
    own Cocoa run loop and blocks the calling thread until done or timed out.
    """
    try:
        from CoreLocation import (
            CLLocationManager,
            kCLAuthorizationStatusDenied,
            kCLAuthorizationStatusNotDetermined,
            kCLAuthorizationStatusRestricted,
        )
        from Foundation import NSObject
    except ImportError:
        return None

    done = threading.Event()
    outcome: dict[str, Any] = {}

    class _Delegate(NSObject):
        def locationManager_didUpdateLocations_(self, manager: Any, locations: Any) -> None:
            coordinate = locations[-1].coordinate()
            outcome["coordinate"] = (coordinate.latitude, coordinate.longitude)
            done.set()

        def locationManager_didFailWithError_(self, manager: Any, error: Any) -> None:
            done.set()

        def locationManagerDidChangeAuthorization_(self, manager: Any) -> None:
            status = manager.authorizationStatus()
            if status in (kCLAuthorizationStatusDenied, kCLAuthorizationStatusRestricted):
                done.set()
            elif status != kCLAuthorizationStatusNotDetermined:
                manager.startUpdatingLocation()

    manager = CLLocationManager.alloc().init()
    delegate = _Delegate.alloc().init()
    manager.setDelegate_(delegate)

    status = manager.authorizationStatus()
    if status in (kCLAuthorizationStatusDenied, kCLAuthorizationStatusRestricted):
        return None
    if status == kCLAuthorizationStatusNotDetermined:
        manager.requestWhenInUseAuthorization()
    else:
        manager.startUpdatingLocation()

    _pump_run_loop_until(done, timeout)
    manager.stopUpdatingLocation()
    return outcome.get("coordinate")


def _reverse_geocode(lat: float, lon: float, timeout: float) -> str | None:
    """Blocking: resolve a coordinate to a city name via Apple's geocoding
    service. Does not require Location Services authorization — the caller
    already has the coordinate; this is just a lookup."""
    try:
        from CoreLocation import CLGeocoder, CLLocation
    except ImportError:
        return None

    done = threading.Event()
    outcome: dict[str, Any] = {}

    def handler(placemarks: Any, error: Any) -> None:
        if placemarks and len(placemarks) > 0:
            outcome["locality"] = placemarks[0].locality()
        done.set()

    geocoder = CLGeocoder.alloc().init()
    location = CLLocation.alloc().initWithLatitude_longitude_(lat, lon)
    geocoder.reverseGeocodeLocation_completionHandler_(location, handler)

    _pump_run_loop_until(done, timeout)
    return outcome.get("locality")


async def current_city() -> str | None:
    """The device's current city via macOS Location Services, or None if it
    can't be determined for any reason (denied, unsupported, timed out)."""
    try:
        coordinate = await asyncio.to_thread(_fetch_coordinate, _LOCATION_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - CoreLocation quirks must not break the briefing
        return None
    if coordinate is None:
        return None
    try:
        return await asyncio.to_thread(
            _reverse_geocode, coordinate[0], coordinate[1], _GEOCODE_TIMEOUT_SECONDS
        )
    except Exception:  # noqa: BLE001
        return None
