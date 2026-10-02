# Theme kit architecture

The existing Platform and classic pages use local ES modules under
`pyharness/ui/theme-kit`. Tokens, preference normalization, Canvas rendering and
lifecycle are separate; host adapters are under `ui/host-adapters`. The kit gets a
container and appearance fields only, never model, account, session or credential
APIs. No CDN, framework migration or business-DOM replacement is involved.

Engine contract: `updateConfig`, `resize`, `pause`, `resume`, `stop`, `destroy`,
and diagnostic `stats`. One WeakMap-owned instance, one RAF and idempotent disposal
per container. Destroy removes Canvas and ResizeObserver. Count is capped at 80,
pixel ratio at 2 and logical size at 3840×2160. Decorations ignore pointer and
accessibility navigation. Business code does not schedule animation frames.

Pause reasons compose: original theme, user-off, low-motion, reduced-motion,
document-hidden, host-hidden and explicit stop. Clearing one cannot clear others.
Missing particle modules keep static styling. Selectors are scoped to the theme
root; original light/dark remain available.

PreferenceStore `load/save/reset` accepts only appearance fields. Browser storage
uses a dedicated namespace, WebView an atomic whitelisted JSON file, and Native
its own QSettings file. Storage failures do not block the host. Native uses
QSS/QPainter with a separate bounded timer, not Web JavaScript.

Browser automation, hidden WebView2, Qt offscreen, installed resources and physical
device testing are separate evidence. Headless RAF timing is not a device frame
rate or total power guarantee.
