// Client ID Metadata Documents (CIMD): `/oauth/client.json` and
// `/oauth/<callbackId>/client.json`.
//
// rpi identifies with these documents for MCP authorization servers that
// support CIMD for public clients (oauth.clientRegistration: "cimd";
// crates/rpi/src/extensions/mcp/oauth.rs — CLIENT_METADATA_BASE_URL and
// callback_id). The document shape mirrors the upstream pi.dev service,
// with rpi's own branding and callback path.
//
// Two forms, exactly like rpi chooses them:
// - the shared document `/oauth/client.json` is used when the AS advertises
//   RFC 9207 `authorization_response_iss_parameter_supported`;
// - otherwise the document is specific to the MCP server URL
//   (`/oauth/<callbackId>/client.json`, callbackId = the first 9 bytes of
//   the SHA-256 of the URL without its fragment, base64url), and the
//   redirect URIs carry that id, so a response cannot be mixed up with one
//   from another authorization server (RFC 9700 section 4.4.2.2).
//   The id is a one-way hash: the service can only validate its shape
//   (base64url, 12 characters), not recompute it — same as upstream.
//
// The redirect URIs use the portless loopback form: RFC 8252 section 7.3
// requires the authorization server to allow any port for loopback
// redirects, which is what lets a single served document cover rpi's
// ephemeral callback port.

const CLIENT_METADATA_BASE_URL = "https://revpi.dev/oauth";
const CALLBACK_PATH = "/callback";
const CALLBACK_ID = /^[A-Za-z0-9_-]{12}$/;

function document(clientId, redirectUris) {
  return {
    client_id: clientId,
    client_name: "rpi",
    client_uri: "https://revpi.dev",
    logo_uri: "https://revpi.dev/favicon.svg",
    application_type: "native",
    redirect_uris: redirectUris,
    grant_types: ["authorization_code", "refresh_token"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
    token_endpoint_auth_methods_supported: ["none"],
  };
}

function json(body) {
  return new Response(JSON.stringify(body, null, 2) + "\n", {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "public, max-age=3600",
    },
  });
}

function notFound() {
  return new Response(null, { status: 404 });
}

export async function onRequestGet({ params }) {
  const path = params.path;
  const segments = Array.isArray(path) ? path : path ? [path] : [];

  if (segments.length === 1 && segments[0] === "client.json") {
    return json(
      document(`${CLIENT_METADATA_BASE_URL}/client.json`, [
        `http://127.0.0.1${CALLBACK_PATH}`,
        `http://localhost${CALLBACK_PATH}`,
      ]),
    );
  }

  if (
    segments.length === 2 &&
    CALLBACK_ID.test(segments[0]) &&
    segments[1] === "client.json"
  ) {
    const callbackId = segments[0];
    return json(
      document(`${CLIENT_METADATA_BASE_URL}/${callbackId}/client.json`, [
        `http://127.0.0.1${CALLBACK_PATH}/${callbackId}`,
        `http://localhost${CALLBACK_PATH}/${callbackId}`,
      ]),
    );
  }

  return notFound();
}