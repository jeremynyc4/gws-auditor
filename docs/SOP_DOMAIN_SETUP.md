# SOP: Adding a New Domain to GWS Security Auditor

This document walks through everything required to run a security audit against
a Google Workspace domain using the `setup_domain.py` script.  It is written
for someone who has not done this before.

**Time estimate:** 30–45 minutes the first time; 15–20 minutes for each
additional domain once the prerequisites are in place.

---

## Overview

The audit tool talks to Google's APIs on your behalf.  Before it can do that,
three things must be in place:

1. A **GCP project** with the required APIs enabled.
2. A **service account** (a machine identity) in that project, with a
   downloadable key file the tool uses to authenticate.
3. **Domain-wide delegation** granted in the Google Admin Console, authorizing
   the service account to read data for your domain.

Steps 1 and 2 are automated by `scripts/setup_domain.py`.  Step 3 requires a
brief manual action in the Admin Console because Google does not expose it
through any API.

---

## Prerequisites

Complete these steps once.  You do not need to repeat them for additional
domains.

### 1. Install Python 3.9 or later

Download from https://python.org/downloads.  During installation on Windows,
check **"Add Python to PATH"**.

Verify it is working:

```
python --version
```

### 2. Install the Google Cloud CLI (gcloud)

Download from https://cloud.google.com/sdk/docs/install and run the installer.

Verify it is working:

```
gcloud --version
```

### 3. Install the audit tool

Clone the repository and install it into a virtual environment:

```
git clone https://github.com/argusssec-cloud/gws-auditor.git
cd gws-auditor
python -m venv .venv
```

Activate the virtual environment:

- **Windows:** `.venv\Scripts\activate`
- **Mac/Linux:** `source .venv/bin/activate`

Install:

```
pip install -e .
```

---

## Per-Domain Setup

Repeat these steps for each new domain.

### Step 1 — Authenticate gcloud for the domain's admin account

The setup script uses `gcloud` to create GCP resources.  You must be
authenticated as the Google account that will own the GCP project.

Run the following two commands.  Both use `--no-launch-browser`, which prints a
URL instead of opening one — this is important if your default browser is signed
into a different Google account.

```
gcloud auth login --no-launch-browser
gcloud auth application-default login --no-launch-browser
```

Each command prints a URL.  Paste that URL into the browser that is signed into
the admin account for this domain, complete the sign-in, and paste the
verification code back into the terminal.

> If your default browser is already signed into the right account, you can omit
> `--no-launch-browser` and the browser will open automatically.

### Step 2 — Run the setup script

From the `gws-auditor` directory, run:

```
python scripts/setup_domain.py <domain> <admin-email>
```

For example:

```
python scripts/setup_domain.py acme.com admin@acme.com
```

The script will:

- Create a GCP project named `acme-com-audit` (derived from the domain name)
- Enable all 10 required APIs on that project
- Create a service account named `gws-auditor`
- Generate a key file and save it to `domains/acme.com/credentials/`
- Write a ready-to-use `domains/acme.com/config.yaml`

If you want to use an existing GCP project instead of creating a new one, add
`--project`:

```
python scripts/setup_domain.py acme.com admin@acme.com --project my-existing-project
```

When the script finishes, it prints something like this:

```
======================================================================
MANUAL STEP REQUIRED: Domain-Wide Delegation
======================================================================

1. Open the Google Admin Console in the browser signed into admin@acme.com:
   https://admin.google.com

2. Navigate to:
   Security → Access and data control → API controls
   → Manage Domain Wide Delegation

3. Click "Add new" and enter:

   Client ID:
     108603865820338527706

   OAuth scopes (paste the entire block below as one line):
     https://www.googleapis.com/auth/admin.directory.domain.readonly,...

4. Click "Authorize".
```

Leave this output visible — you will need the Client ID and scope string in the
next step.

### Step 3 — Configure domain-wide delegation (manual)

1. Open the [Google Admin Console](https://admin.google.com) in the browser
   signed into the **super admin account** for this domain.
2. Navigate to **Security → Access and data control → API controls**.
3. Click **Manage Domain Wide Delegation**.
4. Click **Add new**.
5. Paste the **Client ID** from the script output into the Client ID field.
6. Paste the **OAuth scopes** block from the script output into the scopes field
   (the entire comma-separated string, on one line).
7. Click **Authorize**.

> If a dialog asks whether to overwrite an existing entry for this Client ID,
> click **Yes** only if the Client ID matches the one the script just printed.
> If the existing Client ID is different, do not overwrite it — that entry
> belongs to a different service account.

### Step 4 — Wait for propagation

Google takes up to 15 minutes to activate the delegation.  You can proceed with
other work in the meantime.

### Step 5 — Run the audit

From the `gws-auditor` directory, with the virtual environment active:

```
python -m gws_auditor --config domains/acme.com/config.yaml
```

If you see `unauthorized_client` errors, the delegation has not yet propagated.
Wait a few more minutes and try again.

A successful run finishes with a summary and saves reports to
`domains/acme.com/reports/`.

### Step 6 — Review the results

Open the HTML report in a browser:

```
domains/acme.com/reports/<timestamp>_report.html
```

The report groups findings by section, shows pass/fail status for each check,
and includes remediation guidance for each failure.

---

## Subsequent Runs

The service account key file is **deleted automatically** at the end of each
successful audit run as a security measure.  Before running the audit again,
you must generate a new key:

1. Go to the [GCP Console](https://console.cloud.google.com) → **IAM & Admin →
   Service Accounts**.
2. Select the `gws-auditor` service account in your domain's project.
3. Go to the **Keys** tab → **Add Key → Create new key → JSON → Create**.
4. Save the downloaded file to `domains/<domain>/credentials/` and update the
   `credentials_file` path in `domains/<domain>/config.yaml` if the filename
   changed.

Alternatively, you can re-run `scripts/setup_domain.py` — it will detect that
the project and service account already exist and only generate a new key.

---

## Troubleshooting

**`unauthorized_client` errors when running the audit**
The domain-wide delegation has not fully propagated yet.  Wait up to 15 minutes
and retry.

**`gcloud: command not found`**
The Google Cloud CLI is not on your PATH.  Close and reopen your terminal after
installing it, or add the `gcloud` bin directory to your PATH manually.

**The wrong Google account was used for authentication**
Run `gcloud auth list` to see which account is active.  If it is wrong, run
`gcloud config set account <correct-email>` and then repeat Step 1.

**The script fails with a permission error creating the GCP project**
Your Google account may not have permission to create projects in your
organization.  Ask your GCP administrator to create the project for you, then
pass its ID via `--project` and re-run the script.

**`FileNotFoundError` when running the audit**
The credentials file path in `config.yaml` does not match the actual file
location.  Open `domains/<domain>/config.yaml` and verify the
`credentials_file` value matches the filename in `domains/<domain>/credentials/`.

**Audit completes but many checks show MANUAL status**
Some checks require a Google Workspace Business or Enterprise license.  These
are expected limitations for domains on lower-tier plans.
