# Web Preview

In the Studio’s top bar, click **Preview**. Enter the path to a saved HTML file within the current project and click **Start Preview**. For a new web page, choose a name for a new folder and click **Create starter web**: an actual `index.html` file will be saved, and its preview will open. Existing folders are not overwritten.

- **Edit file** opens the entry HTML file in the editor. Save changes via Cmd/Ctrl+S and return to the preview.
- The preview checks loaded files every two seconds. Upon changes to HTML, CSS, JS, or any other loaded asset, the page is refreshed. It also tracks missing assets you have not yet created.
- **Reload on change** can be disabled to prevent reloading from discarding an in-progress form. **Reload** manually refreshes the page.
- **Mobile · 390 px** adjusts the rendering width. This is not device or mobile browser emulation.
- **Open ↗** opens the web page in a separate tab. Automatic reloading applies only to the embedded preview in Studio, not this new tab.
- Closing the dialog does not stop the server. **Stop** closes the port; exiting Studio also closes it. After restarting Studio, start the preview again.
- Only one preview can run at a time. Another project or tab cannot silently replace it upon Start—you must first use Stop.

## What can be displayed

Standalone HTML/CSS/JS websites and pre-built static builds with local assets, e.g., `dist/index.html`. The folder containing the entry HTML serves as the web root; absolute paths like `/app.js` resolve within this folder. Use a folder designated for public assets. The static server serves common web file types—including JSON, images, and fonts—with a maximum file size of 20 MB; it does not transpile TypeScript/JSX, run npm, Python, PHP, or any application backend, nor does it provide SPA fallback for arbitrary URLs.

External CDNs, remote APIs, embedded pages, and service workers are blocked. The goal is local validation of your own static files. Databases, authentication, payments, or hosting are not added by creating a template. The sample idea list exists only in the page’s memory.

## Separation from Studio

The server uses a different loopback port than Studio and a separate short-lived token. Upon opening, the token is converted into an HttpOnly cookie; access without it is denied. The embedded web page runs in a sandbox with its own Content Security Policy. It has no access to Studio’s DOM or APIs. Hidden files, selected configuration files, backend file types, paths above the root, and symbolic links are inaccessible. Directory opening uses the same symlink replacement protection as the editor. This is not a sandbox for executing arbitrary server-side programs; this feature does not run such programs at all.

## Verification

`studio/tests/test_preview.py` verifies actual HTTP requests, separate ports, tokens, absolute paths to assets, file changes, Stop, concurrent tabs, and path protection. `studio/tests/preview-ui.cjs` verifies reloading and delayed responses upon project changes or preview state changes.

In Chrome, the GUI-based web creation flow, clicking within the app, editing HTML in the editor, saving, and observing the change in the preview were also verified.
