# HASS TTLock Connect

Advanced TTLock lock management for Home Assistant — with full visibility and control over your TTLock cloud API usage.

[<img src="https://img.shields.io/github/v/release/jotaccf/HASS-TTlock-Connect?style=for-the-badge" />](https://github.com/jotaccf/HASS-TTlock-Connect/releases/latest)

## Overview

This integration uses the TTLock Cloud to communicate with your locks. It supports:

- Locking and unlocking
- Discovery of locks on startup
- Real-time updates via a webhook (no battery-draining polling)
- Local Bluetooth state reads when a lock is in range (zero cloud quota)
- Sensors for battery, last operator and last trigger reason
- Passcode management: create, modify, delete, list and clean up expired codes
- IC card and fingerprint management (list, rename, delete)
- Passage mode and auto-lock configuration
- Door sensor support (open/closed state and sensor battery)
- Records history (lock, unlock, etc.)
- **API usage tracking**: live sensors for daily and monthly cloud API calls, with a monthly projection and per-endpoint breakdown
- **Quota-saving polling controls**: every polling interval is tunable, including a webhook-only mode that nearly eliminates polling once webhooks are confirmed working

# Usage

## Requirements

1. A TTLock based smart lock
1. A TTLock gateway (if your lock doesn't have integrated WiFi)
1. Remote unlock must be enabled for each lock
   - This must be done while in Bluetooth range of the lock, from the mobile app
   - Here is a [YouTube video](https://www.youtube.com/watch?v=ni-38QpoNA4) which explains the process

## Creating an OAuth APP

1. Go to https://open.ttlock.com/manager and create an account
1. Register an application (approval can take a few days)
1. Install the integration via HACS: add `jotaccf/HASS-TTlock-Connect` as a custom repository (category: Integration), install it and restart Home Assistant
1. Set up the integration [via the Home Assistant UI](https://my.home-assistant.io/redirect/config_flow_start/?domain=ttlock)
   - The first credentials you will be prompted for are the Application Client ID & Secret created above.
   - The second credentials are the username/password you use to log into the TTLock app on your phone.
1. Once the integration is working you should see a repair notice under Settings > Repairs with the webhook URL
   - Go back to https://open.ttlock.com/manager
   - Select your application and edit the "callback url", entering the webhook URL from the repair notice
   - Test by unlocking your door
   - When the event data is received by Home Assistant the repair notice resolves itself, confirming everything works.

## Managing API usage

TTLock's developer plans have a monthly API-call budget, and multi-lock accounts can exceed it. The integration gives you visibility and control:

- **Usage sensors** — a "TTLock Cloud API" device provides `TTLock API Calls Today` and `TTLock API Calls This Month` sensors. The monthly sensor includes a `projected_month_total` attribute (this month's total at the current rate) and both carry a per-endpoint breakdown, so you can see exactly what is spending your quota.
- **Tunable polling** — under Settings > Devices & Services > TTLock > Configure you can adjust:
  - *Poll interval*: how often each lock's state is re-verified (default 30 min).
  - *Detail refresh interval*: how often slow-changing detail is re-fetched (default 6 h).
  - *Gateway status interval*: how often gateway online/offline status is checked (default 15 min). This is one call per check regardless of lock count — raising it to 60 min saves ~2200 calls/month on its own.
  - *Webhook-only state updates*: once your webhook is confirmed working, skip the per-poll cloud state check entirely and rely on webhooks (and Bluetooth, when in range). Saves one call per lock per poll; only the initial state after a restart comes from the cloud.

# Troubleshooting

## Common issues

1. Invalid client_id
   - Your Application (i.e. OAuth) Client ID & Client Secret for the application you created on open.ttlock.com.
   - These are stored in the "Application Credentials" feature of Home Assistant. If you need to remove/update them, please follow the official [docs](https://www.home-assistant.io/integrations/application_credentials)
   - If you get this error, you need to remove the invalid credentials and re-set up the integration with the correct ones.
1. Invalid username or password
   - The username/password for open.ttlock.com is only used for managing API credentials for the TTLock cloud - do not use these within Home Assistant.
   - The username/password for the TTLock (or 3rd-party branded) mobile app is the account that will work.
1. "Failed to execute the action lock/lock." or "The function is not supported for this lock"
   - This is most likely because you haven't enabled remote unlock; please follow the instructions in the requirements section.
1. Entities are unavailable and debug logs show `hasGateway: 0`
   - TTLock cloud can occasionally lose the gateway association for a lock.
   - First, reboot your TTLock gateway/hub (for example, G2) and re-check the integration.
   - If that does not fix it, remove and re-add the lock in the TTLock app.

## Reporting issues

When reporting issues, please attach the diagnostic information and consider enabling debug logging to provide extra information.

## Development

You can find all the TTLock API calls documented at https://euopen.ttlock.com/document
