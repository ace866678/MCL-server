# Firebase integration starter

This repository now contains a Firebase starter configuration, but **the live app is not switched to Firebase**. Current account/session flows and agent API still use Supabase. The demo console at /demo is local-only and can be used before setting up either provider.

## Starter files

- `firebase.json` — Firebase CLI points to the Firestore rules/indexes and local emulators.
- `.firebaserc.example` — project alias template; copy to `.firebaserc` and replace the example project ID.
- `firestore.rules` — restrictive first-pass rules. Users can read/update their own limited profile; server documents are owner-readable, while server writes, agents, commands, and events are denied.
- `firestore.indexes.json` — empty index manifest to extend only when real queries require it.
- `web/lib/firebase/config.ts` — dependency-free config detection. It does not initialize or import Firebase, so it will not break the existing build when keys are absent.
- `web/lib/firebase/*.ts.example` — future client initialization, email/password auth wrappers, and owner-scoped server listing examples. These examples are deliberately not compiled yet.

## When ready to connect Firebase

1. Create/select a Firebase project and register a Web App in Firebase Console.
2. Enable the sign-in providers you want under Authentication. The auth wrapper example assumes Email/Password is enabled.
3. Copy `.firebaserc.example` to `.firebaserc`, replace its project ID, and check that the selected Firebase project is correct before deploying anything.
4. Copy `web/.env.example` to `web/.env.local`. Fill the six `NEXT_PUBLIC_FIREBASE_*` values from Project settings → Your apps. Add the same values to Vercel only when the Firebase integration is ready to test.
5. Install the client SDK from the web workspace with `npm --prefix web install firebase`. Commit the updated `web/package.json` and `web/package-lock.json`.
6. Rename `web/lib/firebase/client.ts.example` to `client.ts`; rename `auth.ts.example` to `auth.ts` and `firestore.ts.example` to `firestore.ts`. Review the examples against the installed SDK version and your desired sign-in methods.
7. Implement and test a deliberate auth adapter for the current pages. Do not simply remove Supabase env checks; the present dashboard queries Supabase tables and the local agent is wired to the Supabase Edge Function.
8. Test locally with the emulators: `firebase emulators:start --only auth,firestore`. Deploy rules/indexes only after reviewing them: `firebase deploy --only firestore:rules,firestore:indexes`.

## Server and agent security model

The web config/API key is not an authorization boundary. Firestore rules and server-side checks must enforce ownership.

- Keep server-service credentials, agent tokens, bridge tokens, and RCON credentials out of browser config, Firestore-readable documents, and Git.
- The starter rules intentionally **do not let the client create/update/delete Minecraft servers or write commands**. Those operations should go through a trusted Cloud Function or other server-side API that verifies the Firebase ID token, checks server ownership, validates the command against an allowlist, and records an audit event.
- Never let the browser send arbitrary shell strings to an agent. Keep the agent's fixed argv/no-shell design and command allowlist.
- Before migrating the agent, implement the Firebase-side equivalent of the existing agent API and test enrollment, authentication, heartbeat, command polling, acknowledgements, and revocation end-to-end.
- Do not run Supabase and Firebase writes for the same production server until an explicit migration/rollback plan is in place.

Firebase web config contains public project identifiers, not admin secrets. Do not create a service-account key just for the browser; if a future server-side Firebase Admin SDK is needed, store credentials in the platform's secret manager and never in `NEXT_PUBLIC_*`.
