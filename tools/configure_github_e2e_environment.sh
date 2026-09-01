#!/usr/bin/env bash

set -euo pipefail

readonly ENVIRONMENT_NAME="e2e"
readonly CONFIG_PATH="workers/wrangler.e2e.jsonc"
readonly WRANGLER_PATH="./node_modules/.bin/wrangler"

cf_account_id=""
cf_api_token=""
internal_api_token=""
worker_url=""
worker_url_sha256=""

clear_inputs() {
  unset cf_account_id cf_api_token internal_api_token worker_url worker_url_sha256
}

trap clear_inputs EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'configure_github_e2e_environment_failed:%s\n' "$1" >&2
  exit 1
}

contains_line_break() {
  [[ "$1" == *$'\n'* || "$1" == *$'\r'* ]]
}

assert_expected_environment_names() {
  local repo="$1"
  local name

  while IFS= read -r name
  do
    case "$name" in
      CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN|INTERNAL_API_TOKEN)
        ;;
      *)
        fail "unexpected_environment_secret"
        ;;
    esac
  done < <(gh secret list --env "$ENVIRONMENT_NAME" --repo "$repo" --json name --jq '.[].name')

  while IFS= read -r name
  do
    case "$name" in
      E2E_WORKER_URL|E2E_WORKER_URL_SHA256)
        ;;
      *)
        fail "unexpected_environment_variable"
        ;;
    esac
  done < <(gh variable list --env "$ENVIRONMENT_NAME" --repo "$repo" --json name --jq '.[].name')
}

if [[ ! -t 0 || ! -t 1 ]]
then
  fail "interactive_terminal_required"
fi

command -v gh >/dev/null 2>&1 || fail "gh_not_found"
command -v node >/dev/null 2>&1 || fail "node_not_found"
[[ -x "$WRANGLER_PATH" ]] || fail "wrangler_not_found"
[[ -f "$CONFIG_PATH" ]] || fail "e2e_config_not_found"
gh auth status >/dev/null 2>&1 || fail "github_auth_required"

origin_url=$(git remote get-url origin 2>/dev/null) || fail "origin_remote_not_found"
case "$origin_url" in
  https://github.com/*|git@github.com:*)
    ;;
  *)
    fail "unsupported_origin_url"
    ;;
esac
repo=$(gh repo view "$origin_url" --json nameWithOwner --jq .nameWithOwner)
permission=$(gh repo view "$origin_url" --json viewerPermission --jq .viewerPermission)
[[ "$permission" == "ADMIN" ]] || fail "origin_admin_required"

can_admins_bypass=$(gh api "repos/$repo/environments/$ENVIRONMENT_NAME" --jq .can_admins_bypass)
reviewer_count=$(
  gh api "repos/$repo/environments/$ENVIRONMENT_NAME" \
    --jq '[.protection_rules[] | select(.type == "required_reviewers") | .reviewers[]] | length'
)
[[ "$can_admins_bypass" == "false" ]] || fail "admin_bypass_must_be_disabled"
[[ "$reviewer_count" -ge 1 ]] || fail "required_reviewer_missing"

assert_expected_environment_names "$repo"

IFS= read -r -s -p 'Cloudflare account ID: ' cf_account_id
printf '\n' >&2
IFS= read -r -s -p 'Cloudflare API token: ' cf_api_token
printf '\n' >&2
IFS= read -r -s -p 'INTERNAL_API_TOKEN: ' internal_api_token
printf '\n' >&2
IFS= read -r -s -p 'E2E Worker URL: ' worker_url
printf '\n' >&2

[[ "$cf_account_id" =~ ^[0-9a-fA-F]{32}$ ]] || fail "invalid_cloudflare_account_id"
[[ -n "$cf_api_token" ]] || fail "empty_cloudflare_api_token"
[[ -n "$internal_api_token" ]] || fail "empty_internal_api_token"
contains_line_break "$cf_api_token" && fail "invalid_cloudflare_api_token"
contains_line_break "$internal_api_token" && fail "invalid_internal_api_token"
contains_line_break "$worker_url" && fail "invalid_worker_url"

worker_url_record=$(
  printf '%s' "$worker_url" | node -e '
const { createHash } = require("crypto");
const fs = require("fs");
const raw = fs.readFileSync(0, "utf8").trim();
let parsed;
try {
  parsed = new URL(raw);
} catch {
  process.exit(1);
}
const labels = parsed.hostname.toLowerCase().split(".");
const valid = parsed.protocol === "https:"
  && !parsed.username
  && !parsed.password
  && parsed.pathname === "/"
  && !parsed.search
  && !parsed.hash
  && labels.length >= 4
  && labels[0] === "ie-event-bot-e2e"
  && labels.at(-2) === "workers"
  && labels.at(-1) === "dev";
if (!valid) {
  process.exit(1);
}
const origin = parsed.origin;
const fingerprint = createHash("sha256").update(origin).digest("hex");
process.stdout.write(`${origin}\t${fingerprint}`);
'
) || fail "invalid_worker_url"
IFS=$'\t' read -r worker_url worker_url_sha256 <<< "$worker_url_record"
[[ "$worker_url_sha256" =~ ^[0-9a-f]{64}$ ]] || fail "invalid_worker_url_fingerprint"

if ! CLOUDFLARE_ACCOUNT_ID="$cf_account_id" \
  CLOUDFLARE_API_TOKEN="$cf_api_token" \
  "$WRANGLER_PATH" secret list \
    --config "$CONFIG_PATH" \
    --format json 2>/dev/null | node -e '
const fs = require("fs");
const expected = new Set([
  "DISCORD_GUILD_ID",
  "DISCORD_TOKEN",
  "EVENT_CREATE_CHANNEL_ID",
  "EVENT_CREATE_ROLE_ID",
  "GCAL_WEBHOOK_TOKEN",
  "GOOGLE_CALENDAR_ID",
  "GOOGLE_SERVICE_ACCOUNT_JSON_B64",
  "INTERNAL_API_TOKEN",
  "NOTION_EVENT_INTERNAL_ID",
  "NOTION_QA_ID",
  "NOTION_TOKEN",
  "QA_CHANNEL_ID",
  "REMINDER_CHANNEL_ID",
  "REMINDER_ROLE_ID",
]);
const items = JSON.parse(fs.readFileSync(0, "utf8"));
const names = new Set(items.map((item) => item.name));
const valid = items.length === expected.size
  && items.every((item) => item.type === "secret_text" && expected.has(item.name))
  && [...expected].every((name) => names.has(name));
process.exit(valid ? 0 : 1);
' >/dev/null
then
  fail "cloudflare_token_preflight_failed"
fi

printf '%s' "$cf_account_id" |
  gh secret set CLOUDFLARE_ACCOUNT_ID --env "$ENVIRONMENT_NAME" --repo "$repo"
printf '%s' "$cf_api_token" |
  gh secret set CLOUDFLARE_API_TOKEN --env "$ENVIRONMENT_NAME" --repo "$repo"
printf '%s' "$internal_api_token" |
  gh secret set INTERNAL_API_TOKEN --env "$ENVIRONMENT_NAME" --repo "$repo"
printf '%s' "$worker_url" |
  gh variable set E2E_WORKER_URL --env "$ENVIRONMENT_NAME" --repo "$repo"
printf '%s' "$worker_url_sha256" |
  gh variable set E2E_WORKER_URL_SHA256 --env "$ENVIRONMENT_NAME" --repo "$repo"

secret_count=$(
  gh secret list --env "$ENVIRONMENT_NAME" --repo "$repo" --json name --jq 'length'
)
variable_count=$(
  gh variable list --env "$ENVIRONMENT_NAME" --repo "$repo" --json name --jq 'length'
)
[[ "$secret_count" -eq 3 ]] || fail "environment_secret_count_mismatch"
[[ "$variable_count" -eq 2 ]] || fail "environment_variable_count_mismatch"

printf 'github_e2e_environment_configured secrets=%s variables=%s\n' \
  "$secret_count" \
  "$variable_count"
