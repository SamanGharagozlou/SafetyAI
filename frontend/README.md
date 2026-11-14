# ClawMetry frontend — Phase 1

Mobile-first visitor welcome prototype for the SafetyAI Robotics Hackathon. Built with React, Vite, TypeScript, and Tailwind CSS (via its Vite plugin).

## Run locally

Use Node.js 22.12+ (Node 24 recommended) and npm. From the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open the local URL printed by Vite (normally http://localhost:5173).

For a phone on the same trusted network, run `npm run dev -- --host 0.0.0.0` and open the printed network URL. This exposes the development server to your network.

## Production build

```sh
npm run build
npm run preview
```

The build first checks TypeScript, then writes static assets to `dist/`. Preview normally serves at http://localhost:4173. Deploy the contents of `dist/` using a static host when deployment is separately authorized.

## Included behavior

- Welcome screen with an optional nickname (maximum 40 characters).
- Empty or whitespace-only nicknames continue as a guest.
- Get Started opens an explicitly labeled interaction placeholder.
- Back returns to the welcome screen, retaining the in-memory nickname.
- Privacy information opens an in-app page and returns to the previous view.
- Page changes move keyboard focus to the heading; controls have visible focus, explicit labels, and touch-friendly sizes. Animations respect reduced-motion preferences.

## Privacy and scope

Nicknames are held only in React state. Refreshing or closing the page clears them. The frontend does not use cookies, local/session storage, analytics, external fonts, trackers, backend requests, microphone access, speech recognition, or robot control. Artwork is inline SVG. A hosting provider may retain request logs; its practices are outside this frontend's scope and have not been verified. No GDPR compliance claim is made.

The frontend has no FastAPI or robot SDK dependency. Future speech, robot responses, QR entry provisioning, and hardware integration are outside Phase 1. UI navigation is in-memory and does not require server-side routes.

## Files

- `src/main.tsx`: application views, state, and local SVG artwork.
- `src/styles.css`: Tailwind import, responsive visual styling, reduced-motion handling.
- `vite.config.ts`: React and Tailwind plugins.
- `tsconfig.json`: strict TypeScript checks.
- `index.html`: document metadata and application entry.
- `package.json`, `package-lock.json`: commands and pinned dependency resolution.
- `.gitignore`: excludes generated files, dependencies, and local environment files.
