# Personal Mail

这个项目只服务我自己。不用管其他用户会怎么想，想咋做就咋做。我就想自己用着爽。

A minimal desktop Gmail reader with a centered list, light/dark appearance, safe HTML reading, and Jev filtering. This repository contains the web app; the older native prototype stays local.

## Run locally

```sh
python3 server.py
```

Open http://127.0.0.1:5173/. Python 3.11+ and the standard library are sufficient. Keep the terminal running; Ctrl+C stops it.

## Private setup

All credentials, email content, labels, and personal preferences live in ignored `.local/` files. Never commit them.

- Save a Google Web OAuth client in `.local/google.json` using Google's `web` JSON wrapper. Enable Gmail API and register `http://127.0.0.1:5173/auth/callback`. The app requests only `gmail.readonly`.
- Set `MAIL_ALLOWED_ACCOUNT` or put `{"allowed_account":"reader@example.com"}` in `.local/personal.json`. This allowlist is required before account access is accepted.
- Run `python3 scripts/configure-jev.py` to enter the TypeSafe key without echo or shell-history exposure. Alternatively set `TYPESAFE_API_KEY`. `JEV_MODEL` overrides the pinned `jev-1.13.0` model.
- Personal filtering rules live in `.local/filter-prompt.txt`. If absent, the generic `filter-prompt.example.txt` is used. Editing Prompt on the page and clicking Run filter saves the private copy.

The service binds only to loopback. Local files use owner-only permissions, but are not separately encrypted. Keys and tokens never go to the browser. Google testing-mode credentials may require reconnection.

## Behavior

- Fetch the latest 100 inbox messages on launch, foreground return, and every 30 seconds while visible.
- Messages ever marked keep manually or by AI are retained beyond the recent-message window. Resets and reclassification do not remove retained copies.
- Reading state is local; Gmail is never archived, modified, or deleted.
- Jev directly selects keep, hide, or uncertain. Confidence and probabilities are for inspection only; no threshold overrides its choice. Errors remain visible.
- Only necessary email headers and body text are sent to TypeSafe; attachments are excluded. Recognized OTPs are handled locally. Email content is untrusted data.
- Checkbox means expected keep; unchecked means expected hide. Run filter compares model results with those labels. Green marks are matches, not independently measured accuracy.
- Show filtered controls hidden decisions. Manually checked keep messages always remain visible.
- Reset filter clears decisions and pauses automatic filtering. It preserves labels, reading state, and retained cache.
- MIME and forwarding parsing, sanitized HTML in a scriptless sandbox, embedded images, attachment downloads, and offline cached reading are supported. Remote images are blocked.

## Verification

```sh
python3 -m unittest discover -s tests -v
node --check app.js
```

Tests cover parsing, sanitization, classification validation and fallback, local read state, cache retention, labels, reset cancellation, OAuth scope, and request guards. API specification: https://docs.typesafe.ai/api.
