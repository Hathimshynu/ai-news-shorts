"""One-time helper (Google Colab): get a YouTube refresh token that can upload videos, set thumbnails
AND upload caption files. Upload your client_secret.json in Colab's Files panel, then run this cell.
Put the printed token in the GitHub secret YT_REFRESH_TOKEN."""
# !pip install -q google-auth-oauthlib   (uncomment in Colab)
from urllib.parse import unquote

from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=["https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.force-ssl"],
    redirect_uri="http://localhost")
url, _ = flow.authorization_url(access_type="offline", prompt="consent")
print("Open this URL, sign in with the channel's Google account, allow access:\n", url)
code = unquote(input("Paste the part after code= and before &scope from the localhost address bar: ").strip())
flow.fetch_token(code=code)
print("\nYT_REFRESH_TOKEN =", flow.credentials.refresh_token)
