/**
 * Public Firebase configuration scaffold.
 *
 * This helper deliberately does not import or initialize the Firebase SDK, so
 * the current Supabase-backed app keeps building until Firebase is integrated.
 * Firebase web config values are public identifiers; never put Admin SDK
 * credentials or service-account JSON in NEXT_PUBLIC_* variables.
 */

export type FirebasePublicConfig = {
  apiKey: string;
  authDomain: string;
  projectId: string;
  storageBucket: string;
  messagingSenderId: string;
  appId: string;
};

export function getFirebasePublicConfig(): FirebasePublicConfig | null {
  // Keep each environment access static so Next.js can inline NEXT_PUBLIC_* values.
  const config = {
    apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
    authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
    projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
    storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
    messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
    appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  };

  const entries = Object.entries(config);
  const configuredCount = entries.filter(([, value]) => Boolean(value?.trim())).length;

  // Missing Firebase configuration is expected until the later integration.
  if (configuredCount === 0) return null;

  const missing = entries
    .filter(([, value]) => !value?.trim())
    .map(([key]) => key);

  if (missing.length) {
    throw new Error(
      "Firebase configuration is incomplete. Missing: " + missing.join(", ") + ".",
    );
  }

  return config as FirebasePublicConfig;
}

export function firebaseConfigured(): boolean {
  return getFirebasePublicConfig() !== null;
}
