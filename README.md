# Mail

A quieter inbox. A minimal Gmail reader that uses AI to surface the messages you want to read.

Mail runs locally in your browser. It pairs a spacious, single-line email list with a focused reading view, light and dark themes, and an editable filter powered by Jev. Your preferences decide what stays visible.

## How it works

Connect Gmail, describe what matters to you, and let Mail classify incoming messages:

| Result | Meaning |
| --- | --- |
| ✓ Keep | A message worth keeping in view |
| − Uncertain | A message that needs your judgment |
| × Hide | A message that can stay out of the way |

Turn off **Show filtered** for a quieter list, or turn it on to inspect every result. Messages you manually check to keep stay visible either way. Confidence and probabilities are available on hover; they do not override the model’s decision.

Filtering changes what you see in Mail. It does not archive, delete, or relabel anything in Gmail. Reading a message marks it read locally only.

## Features

- **Focused reading.** Sender, subject, and time share one line. Open a message to read it without a permanent sidebar or split pane.
- **Forwarding support.** Recover original sender, recipient, and date from supported forwarding formats, with a persistent color accent for each source mailbox.
- **Light and dark themes.** Follow your system appearance or switch manually.
- **A filter you can inspect.** Edit the prompt, mark expected results, and compare them with the model’s choices.
- **Persistent local cache.** Keep the latest 100 inbox messages, plus messages ever marked keep manually or by AI. Those retained copies survive filter resets and reclassification.
- **Offline reading.** Read cached messages without an internet connection while the local server is running.

Mail currently focuses on reading and filtering. Sending, replying, and background push notifications are not implemented.

## Get started

You need **Python 3.11+**, a Gmail account, a Google Web OAuth client, and a TypeSafe API key. The server uses the Python standard library; there are no Python packages or frontend build steps to install.

### 1. Clone the repository

```sh
git clone https://github.com/daniezl/mail.git
cd mail
mkdir -p .local
chmod 700 .local
```

### 2. Configure Gmail access

In [Google Cloud Console](https://console.cloud.google.com/), enable the Gmail API, configure the OAuth consent screen, and create an OAuth client of type **Web application**.

Add this authorized redirect URI:

```text
http://127.0.0.1:5173/auth/callback
```

Download the client JSON and save it as `.local/google.json`, preserving the top-level `web` object. If your OAuth app is in testing mode, add your Gmail account as a test user. Testing-mode credentials may require periodic reconnection.

Create `.local/personal.json` with the account allowed to connect:

```json
{
  "allowed_account": "you@gmail.com"
}
```

Then restrict access to both files:

```sh
chmod 600 .local/google.json .local/personal.json
```

Alternatively, set `MAIL_ALLOWED_ACCOUNT` in the server’s environment. Mail requests only the `gmail.readonly` scope.

### 3. Configure Jev

Run the hidden-input setup script:

```sh
python3 scripts/configure-jev.py
```

The key is saved to `.local/jev.json`. It is not entered through the webpage or included in shell history. You can also provide `TYPESAFE_API_KEY` through the server’s environment.

The default model is pinned to `jev-1.13.0`; set `JEV_MODEL` to override it. Mail uses TypeSafe’s [Choice API](https://docs.typesafe.ai/api) with three outcomes: keep, hide, and uncertain.

### 4. Start Mail

```sh
python3 server.py
```

Open [127.0.0.1:5173](http://127.0.0.1:5173/) and click **Connect Gmail**. Keep the terminal open while using Mail; press **Ctrl+C** to stop the server.

Mail syncs on startup, when you return to the page, and every 30 seconds while the page is visible.

## Search

Click the search icon, enter a query, and press Enter. Search queries Gmail directly, including archived mail and messages hidden by the AI filter. Gmail operators such as `from:` and `subject:` work too. Use **Load more** for additional results. Reading and returning preserves your search; Escape closes the reader first, then search. Search requires a connection and uses temporary memory without changing the inbox cache or classification labels.

## Tune the filter

Expand **Prompt** to describe the messages you want to keep. The generic starting rules are in [`filter-prompt.example.txt`](filter-prompt.example.txt); your saved rules live privately in `.local/filter-prompt.txt`.

1. Turn on **Show filtered** to review all cached messages.
2. Check messages you expect to keep. Unchecked messages are treated as expected hides.
3. Edit the prompt and click **Run filter** to save it and reclassify the cache.
4. Review the green result marks and the correct count beside the button.

The count measures agreement with your current checkboxes, not accuracy on an independent test set. New, unchecked messages count as expected hides until you label them. Uncertain results do not count as correct.

**Reset filter** clears classifications and pauses automatic filtering, including across page reloads. It preserves your checkboxes, local reading state, and retained messages. Click **Run filter** to resume. Each rerun makes fresh classification requests.

## Data and privacy

The server listens only on `127.0.0.1`. It is intended to run on your computer, not as a publicly hosted service.

- **Local storage:** OAuth credentials, API keys, cached mail, labels, and personal rules stay in the ignored `.local/` directory. Files use owner-only permissions but are not separately encrypted.
- **AI processing:** Necessary email headers and body text are sent to TypeSafe for classification. Attachments are excluded. Recognized verification codes are handled locally.
- **Failure behavior:** Failed or invalid classifications remain visible as uncertain. Email content is treated as data, never as instructions to execute.
- **Email rendering:** HTML is sanitized and displayed in a scriptless sandbox. Embedded images and attachment downloads are supported; remote images are blocked.
- **Repository contents:** Credentials, cached messages, personal prompts, and local native-project files are excluded from version control. Review changes before publishing your own configuration or examples.

Local hosting does not mean all processing is local: Gmail access and Jev classification require their respective external services.

## Development

| File | Responsibility |
| --- | --- |
| `server.py` | Local HTTP server, OAuth callback, session and request guards |
| `webmail/service.py` | Gmail sync, SQLite cache, labels, and Jev classification |
| `webmail/parser.py` | MIME parsing, forwarding metadata, and HTML sanitization |
| `app.js`, `index.html`, `styles.css` | Browser interface |
| `tests/test_webmail.py` | Backend regression tests |

Run checks with:

```sh
python3 -m unittest discover -s tests -v
node --check app.js
```

Node.js is only needed for the JavaScript syntax check. Tests cover parsing, sanitization, classification validation and fallback, read state, cache retention, filter resets, OAuth scope, and request guards.
