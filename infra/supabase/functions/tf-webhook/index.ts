// infra/supabase/functions/tf-webhook/index.ts
// Always-on Supabase Edge Function to receive TinyFish webhook events, persist them in monitor_events table,
// and trigger a fire-and-forget wake call to the Render Time Machine processor.

import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.39.0";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const WEBHOOK_SECRET = Deno.env.get("WEBHOOK_SECRET") ?? Deno.env.get("TINYFISH_WEBHOOK_SECRET") ?? "";
const APP_URL = Deno.env.get("APP_URL") ?? "";
const WAKE_SECRET = Deno.env.get("WAKE_SECRET") ?? "";

serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" }
    });
  }

  // 1. Validate secret if configured (via query param ?secret=... or X-Webhook-Secret header)
  const url = new URL(req.url);
  const secretParam = url.searchParams.get("secret");
  const secretHeader = req.headers.get("x-webhook-secret");
  if (WEBHOOK_SECRET) {
    if (secretParam !== WEBHOOK_SECRET && secretHeader !== WEBHOOK_SECRET) {
      return new Response(JSON.stringify({ error: "Unauthorized: Invalid or missing webhook secret" }), {
        status: 401,
        headers: { "Content-Type": "application/json" }
      });
    }
  }

  try {
    const payload = await req.json();
    const runId = payload.id ?? payload.run_id ?? payload.data?.run_id ?? null;
    const monitorId = url.searchParams.get("monitor_id") ?? payload.fetch_monitor_id ?? payload.monitor_id ?? payload.data?.monitor_id ?? null;

    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

    const { data, error } = await supabase
      .from("monitor_events")
      .insert({
        monitor_id: monitorId,
        run_id: runId,
        payload: payload,
        processed: false
      })
      .select("id")
      .single();

    if (error) {
      console.error("Failed to insert monitor_event:", error);
      return new Response(JSON.stringify({ error: error.message }), {
        status: 500,
        headers: { "Content-Type": "application/json" }
      });
    }

    // 2. Fire-and-forget wake notification to Render API processor
    if (APP_URL) {
      const wakeEndpoint = `${APP_URL.replace(/\/$/, "")}/internal/wake${WAKE_SECRET ? `?secret=${encodeURIComponent(WAKE_SECRET)}` : ""}`;
      try {
        fetch(wakeEndpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(WAKE_SECRET ? { "x-wake-secret": WAKE_SECRET } : {})
          },
          signal: AbortSignal.timeout(3000)
        }).catch(() => {});
      } catch {
        // ignore background fetch error
      }
    }

    return new Response(JSON.stringify({ ok: true, id: data.id }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  } catch (err: any) {
    console.error("Webhook processing error:", err);
    return new Response(JSON.stringify({ error: err.message || "Invalid payload" }), {
      status: 400,
      headers: { "Content-Type": "application/json" }
    });
  }
});
