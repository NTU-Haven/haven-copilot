# Playback of browser-recorded webm: seek check (ticket 09, spec 11.3 Validation Gate)

## Question

Can Chrome seek (jump to `segment.start`) in a recording made by the page's MediaRecorder and
stored by Haven, when the file is served over HTTP `Range` from `GET /media/audio/<ticket>`?

## What could be checked without a browser

Not run in Chrome (this was written where no reliable Chrome was available). From how the file is made:

- The page records with `MediaRecorder` and sends it in chunks (`/api/recordings/<id>/chunk`);
  the server concatenates them into one `.webm`.
- A MediaRecorder stream is written live: the EBML header has an unknown segment size, there is
  **no Cues element** (the seek index) and often **no Duration**. Chrome then plays the file but
  shows `duration = Infinity` and a seek (`currentTime = x`) either jumps to the start or is
  ignored, even though the server answers Range correctly. This is a well-known Chromium
  behaviour (crbug 642012), not something a Range server can fix.
- Haven has no ffmpeg dependency (spec 11.3 option a needs one on the B70 host and must not add a
  Python dependency), so the file cannot be remuxed here.

Server side is covered by `tests/test_playback.py`: `Range: bytes=0-1023`, `a-`, `-n`, 416,
expiry, deleted audio, other therapist's ticket. That proves the transport; it does not prove the
browser can seek in that container.

## Decision

Treat browser-recorded webm as **not seekable** until the manual check below says otherwise
(spec 11.3 option b):

- `playback_supported` in the session view is `false` for `audio/webm`; `true` for uploaded
  m4a / mp3 / wav / ogg / flac; `null` when the session never had audio.
- `POST .../audio/ticket` for a webm answers 409 `audio_status: "unsupported"`.
- The UI shows 「瀏覽器錄製的錄音不支援回放…」 in the player bar and no play buttons.
- Uploaded files keep full segment playback.

**Ticket 10 (ffmpeg remux / duration fix): optional.** Do it only if therapists mostly record in
the browser and the check below fails and ffmpeg is already on the host. To enable webm later,
remove `"audio/webm"` from `playback.UNSUPPORTED_MIMES` (one line) after the remux step.

## Manual Chrome check (about 10 minutes)

1. `HAVEN_LLM=demo python -m haven`, open http://127.0.0.1:8765 in Chrome.
2. Record 30-60 seconds in the browser (new recording), finish, open the session.
3. Temporarily set `UNSUPPORTED_MIMES = ()` in `haven/playback.py` and restart.
4. Click the play button of a line near the end. Pass if playback starts within a second of that
   line's time and the player's progress bar can be dragged. Fail if it starts at 0:00, the
   duration shows as Infinity / `--:--`, or dragging does nothing.
5. In DevTools console: `document.querySelector("#player-bar audio").duration` should be a finite number.
6. Also try one uploaded `.m4a` or `.mp3`: it must pass (this is the supported path).
7. Record the result here (browser version, pass/fail) and restore `UNSUPPORTED_MIMES`.

Result: _not yet run in Chrome._
