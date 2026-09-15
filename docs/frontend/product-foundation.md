# Rpy frontend product foundation

## Product decision record

The product brief intentionally leaves audience, brand references and several feature choices open. Until product research says otherwise, the MVP takes the conservative path below instead of inventing backend capabilities.

### Primary user and job

Primary user: legal professional (lawyer, paralegal or internal legal team member) who already knows the CNJ number and needs to understand the latest available process state quickly.

Primary job: find an already-ingested, tenant-authorized process by CNJ, verify its basic identity and read the latest published AI summary without traversing every movement manually.

This is deliberately narrower than case management. Rpy is an intelligence/read surface in this phase, not a docketing, deadline or document-management system.

### MVP journey

1. Enter the environment credential.
2. Search by canonical CNJ or 20 digits.
3. See explicit loading/error/permission/not-found feedback.
4. Confirm CNJ, class and court.
5. Read the latest published summary, or a clear no-summary state.
6. Copy the summary when needed.
7. Run another lookup without losing orientation.

### Information architecture

The current backend supports one product surface, so the MVP remains a single focused workspace rather than a fabricated multi-page dashboard:

- global header: product identity and access status;
- lookup workspace: CNJ search as the primary action;
- process identity: CNJ, class and court;
- summary document: model-generated content and provenance available from the API;
- contextual actions: copy summary and new lookup;
- access setup: technical bearer credential, clearly separated from process search.

A normal login, dashboard, recents, favorites, alerts, profile, administration and case-management navigation are deferred until real contracts exist.

## Existing backend inventory

### Public/product endpoints

- `GET /health`: process liveness.
- `GET /ready`: database-backed readiness.
- `GET /processes/{code}`: bearer-authenticated tenant-scoped process read. Accepts canonical CNJ or 20 digits. Returns `code`, `class_name`, `court` and either the current-version summary or `null`.
- `POST /webhooks/judit/{token}`: provider webhook ingestion. It is not a user-facing process-request endpoint.
- `GET /ops/metrics`: operations-only endpoint protected by a separate ops token; it does not belong in the end-user UI.

### Data already stored but not exposed by the product endpoint

The current process model persists structured `parties`, `subjects`, `header`, `secrecy_level`, `updated_at` and current-version `process_steps`. Judit normalization also preserves selected header fields such as instance, area, county/state/city and amount. These fields require an explicit, tenant-safe API contract before the frontend may use them.

### Summary data already exposed

The current summary object exposes `markdown`, `validation`, `model`, `prompt_version`, `generation_ms` and `created_at`. The UI should prioritize the legal content and use provenance as secondary information rather than exposing implementation noise by default.

## Capability classification

### Implementable now

- CNJ-only lookup;
- process identity (CNJ/class/court);
- published summary reading;
- no-summary state;
- explicit auth/validation/not-found/network/error states;
- copy summary client-side;
- accessible loading feedback and retry/new-search actions;
- responsive desktop/mobile layout;
- technical bearer-token access without browser persistence.

### Small backend extension, justified later

- parties/subjects/header/last-updated fields;
- relevant or recent movement timeline;
- explicit generation state distinct from a legitimate `summary: null`;
- process-request/refresh state only after a real Judit outbound request contract exists.

Any extension must remain tenant-scoped and must not leak secret-case data.

### External dependency

- requesting or refreshing a missing CNJ through Judit, because the repository currently implements inbound Judit webhook semantics but no verified outbound request contract;
- production identity provider if normal user login is required.

### Explicitly deferred

- dashboard statistics;
- favorites/watchlists;
- alerts/notifications;
- deadline/task management;
- document viewer/management;
- sharing links;
- PDF/export workflow;
- administration UI;
- speculative AI risk/next-action widgets not represented by the validated summary contract;
- React/Next/Vite migration without demonstrated interaction complexity.

## UX principles

1. Legal reading first. Process identity and summary dominate the hierarchy.
2. Calm density. Desktop is information-efficient without becoming a generic SaaS dashboard.
3. Precision over decoration. No gradients, glass effects, ornamental metrics or card grids without information value.
4. State is content. Loading, empty, error and permission conditions explain what happened and the next valid action.
5. Progressive disclosure. Technical provenance is secondary; user-facing legal content is primary.
6. Safe rendering. Model output remains text-node based and credentials remain in memory only.
7. Keyboard complete. Search, credential visibility, copy and retry/new-search actions must be reachable and have visible focus.
8. Motion is optional and subtle; reduced-motion preference disables nonessential scrolling/transition behavior.

## Design system baseline

### Personality

Until a brand is defined: **precise, sober, intelligent**. Use the wordmark `Rpy`; no invented logo.

### Color

Light-first neutral legal/editorial surface with restrained deep-green accent. Semantic success, warning and danger tokens must meet WCAG 2.2 AA contrast and never communicate state by color alone. Dark mode is deferred until a product requirement exists.

### Typography

Use system sans-serif for controls, metadata and navigation; a conservative system serif stack may be used for long-form summary reading and high-level headings. No external font request is required for the MVP.

### Spacing and sizing

Use a 4px base grid with semantic steps `4, 8, 12, 16, 24, 32, 48, 64`. Interactive controls target at least 44px in the compact dimension. Main reading measure stays approximately 70–80 characters per line.

### Shape and elevation

Borders and spacing establish most hierarchy. Radius is restrained (4–8px) and elevation is reserved for floating/temporary layers; primary content should not look like a pile of cards.

### Breakpoints

- compact: below 720px, single-column and full-width primary actions;
- medium: 720–1023px;
- workspace: 1024px and above, optimized for desktop legal work.

These are layout thresholds, not device assumptions.

### Focus and input states

Every interactive element has a visible `:focus-visible` outline with sufficient contrast and offset. Inputs have persistent labels, concise hints and explicit error association where errors are field-specific. Disabled state is not used as the only loading indicator.

## Required state model

- Initial: explains the lookup requirement without empty dashboard chrome.
- Loading: preserves context, marks the result region busy and states that the latest available version is being consulted.
- Success: moves focus/orientation to the process result without surprising keyboard users.
- Invalid CNJ: identifies the field and preserves entered data for correction.
- Missing credential: identifies/focuses credential input.
- Unauthorized: says the credential is invalid/expired; does not imply whether a process exists.
- Not found/no access: backend currently conflates these through 404, so the UI must preserve that ambiguity rather than leaking authorization information.
- Existing process/no summary: explains that process data exists but no published summary is available; do not label it "processing" because the API cannot prove that state yet.
- Network unavailable: distinct from an HTTP application error; offers retry.
- Server error: concise recovery action, no stack trace.
- Long summary: readable document flow; do not truncate legal content by default.

## Accessibility acceptance criteria

- semantic landmarks and heading order;
- skip link to main content;
- labels for every form control;
- `aria-live` for asynchronous status without duplicate announcements;
- `aria-busy` while loading;
- errors connected to relevant controls when possible;
- visible keyboard focus using `:focus-visible`;
- no interaction requiring pointer hover;
- minimum target sizing appropriate to WCAG 2.2 AA;
- contrast compatible with WCAG 2.2 AA;
- reduced-motion preference respected;
- content and status never distinguished by color alone.

## MVP success criterion

A legal professional who has a valid environment credential and an authorized, already-ingested CNJ can understand whether the lookup succeeded, identify the process and read/copy the current published summary quickly on desktop or mobile, using keyboard alone if necessary, without credential persistence, tenant leakage or executable model output.
