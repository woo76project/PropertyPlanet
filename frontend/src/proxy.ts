import type { NextFetchEvent, NextRequest } from "next/server";

export default async function proxy(request: NextRequest, event: NextFetchEvent) {
  try {
    // OpenNext supplies runtime secrets when a request arrives. Defer Clerk's
    // module initialization until the request is being handled.
    const { clerkMiddleware } = await import("@clerk/nextjs/server");
    return await clerkMiddleware()(request, event);
  } catch (error) {
    let workerSecretPresent: boolean | null = null;
    try {
      const { getCloudflareContext } = await import("@opennextjs/cloudflare");
      const env = getCloudflareContext().env as unknown as Record<string, unknown>;
      workerSecretPresent =
        typeof env.CLERK_SECRET_KEY === "string" && env.CLERK_SECRET_KEY.length > 0;
    } catch {
      // next dev has no Cloudflare request context.
    }
    // Presence checks only: never log credentials, their length, or request data.
    console.error("Clerk runtime configuration: " + JSON.stringify({
      workerSecretPresent,
      processSecretPresent: Boolean(process.env.CLERK_SECRET_KEY),
    }));
    throw error;
  }
}

export const config = {
  matcher: [
    // Skip Next.js internals and static assets.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
