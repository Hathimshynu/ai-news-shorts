"""One-time helper: turn a short-lived token from Meta's Graph API Explorer into a
NON-EXPIRING Page token, and find your Instagram account id.

Run in Google Colab (paste into a Code cell) or locally:  python scripts/instagram_token.py
You need: App ID and App Secret (developers.facebook.com > your app > App settings > Basic)
          and a User token from the Graph API Explorer with these permissions:
          instagram_basic, instagram_content_publish, pages_show_list,
          pages_read_engagement, business_management
"""
import requests

G = "https://graph.facebook.com/v21.0"

app_id = input("App ID: ").strip()
app_secret = input("App Secret: ").strip()
short_token = input("User token from Graph API Explorer: ").strip()

# 1. Short-lived user token -> long-lived user token (60 days)
r = requests.get(f"{G}/oauth/access_token", params={
    "grant_type": "fb_exchange_token", "client_id": app_id,
    "client_secret": app_secret, "fb_exchange_token": short_token}).json()
if "access_token" not in r:
    raise SystemExit(f"Token exchange failed: {r}")
long_user = r["access_token"]

# 2. Pages you manage -> Page token (does not expire when made from a long-lived user token)
pages = requests.get(f"{G}/me/accounts", params={
    "access_token": long_user, "fields": "id,name,access_token,instagram_business_account{id,username}"}).json()
found = False
for p in pages.get("data", []):
    ig = p.get("instagram_business_account")
    print(f"\nPage: {p['name']} (id {p['id']})")
    if not ig:
        print("  No Instagram professional account linked to this Page.")
        continue
    found = True
    print(f"  Instagram: @{ig.get('username')}")
    print("\n==== Add these as GitHub Secrets ====")
    print(f"IG_USER_ID      = {ig['id']}")
    print(f"IG_ACCESS_TOKEN = {p['access_token']}")
    info = requests.get(f"{G}/debug_token", params={
        "input_token": p["access_token"], "access_token": f"{app_id}|{app_secret}"}).json().get("data", {})
    print(f"(token expires_at = {info.get('expires_at')}; 0 means never)")

if not found:
    print("\nNo linked Instagram account found. Check: Instagram is a Professional account, "
          "it's linked to your Facebook Page, and you ticked the Page + Instagram account "
          "when generating the token.", pages.get("error", ""))
