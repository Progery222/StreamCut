import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import settings

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubePublisher:
    def build_auth_url(self, redirect_uri: str, state: str) -> str:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.youtube_client_id,
                    "client_secret": settings.youtube_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=YOUTUBE_SCOPES,
            redirect_uri=redirect_uri,
        )
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
        )
        return url

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": settings.youtube_client_id,
                    "client_secret": settings.youtube_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=YOUTUBE_SCOPES,
            redirect_uri=redirect_uri,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        return {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
        }

    async def upload(
        self,
        token_data: dict,
        video_path: Path,
        title: str,
        description: str,
    ) -> str:
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._upload_sync, token_data, video_path, title, description
        )

    def _upload_sync(
        self,
        token_data: dict,
        video_path: Path,
        title: str,
        description: str,
    ) -> str:
        creds = Credentials(
            token=token_data["token"],
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", settings.youtube_client_id),
            client_secret=token_data.get("client_secret", settings.youtube_client_secret),
        )

        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            _, response = request.next_chunk()

        video_id = response["id"]
        url = f"https://youtube.com/shorts/{video_id}"
        logger.info(f"YouTube upload: {url}")
        return url


class TikTokPublisher:
    """TikTok Content Posting API — требует одобрения приложения."""

    def build_auth_url(self, redirect_uri: str, state: str) -> str:
        client_key = settings.tiktok_client_key
        return (
            f"https://www.tiktok.com/v2/auth/authorize/"
            f"?client_key={client_key}"
            f"&scope=video.publish,video.upload"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        import httpx

        resp = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "open_id": data.get("open_id", ""),
        }

    async def upload(
        self,
        token_data: dict,
        video_path: Path,
        title: str,
        description: str,
    ) -> str:
        import httpx

        access_token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Step 1: Init upload
        async with httpx.AsyncClient() as client:
            init_resp = await client.post(
                "https://open.tiktokapis.com/v2/post/publish/video/init/",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "post_info": {
                        "title": title[:150],
                        "privacy_level": "PUBLIC_TO_EVERYONE",
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_path.stat().st_size,
                    },
                },
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            upload_url = init_data["data"]["upload_url"]
            publish_id = init_data["data"]["publish_id"]

            # Step 2: Upload video
            with open(video_path, "rb") as f:
                upload_resp = await client.put(
                    upload_url,
                    content=f.read(),
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{video_path.stat().st_size - 1}/{video_path.stat().st_size}",
                    },
                )
                upload_resp.raise_for_status()

        logger.info(f"TikTok upload: publish_id={publish_id}")
        return f"tiktok:publish_id:{publish_id}"
