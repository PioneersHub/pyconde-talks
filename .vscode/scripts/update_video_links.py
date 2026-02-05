"""Script to update video links in the database from a JSON file containing Vimeo links."""
import json
from pathlib import Path
import requests
import json
import settings


from talks.models import Talk


vimeo_json_path = Path("vimeo_links.json")
vimeo_json = json.loads(vimeo_json_path.read_text())

ACCESS_TOKEN = "your_access_token_here"
PROJECT_ID = "xxxxxxxx"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

url = f"https://api.vimeo.com/me/projects/{PROJECT_ID}/videos"
params = {
    "fields": "name,player_embed_url,uri",
}

response = requests.get(url, headers=headers, params=params)
data = response.json()

# Export to JSON with embed URLs
video_export = {
    "folder_id": PROJECT_ID,
    "videos": [
        {
            "name": video["name"],
            "link": video["link"],
            "embed_url": video["player_embed_url"]
        }
        for video in data.get("data", [])
    ]
}

with open("vimeo_videos_with_embeds.json", "w", encoding="utf-8") as f:
    json.dump(video_export, f, indent=2, ensure_ascii=False)

print(f"Exported {len(video_export['videos'])} videos with embed URLs")





pretalx_to_vimeo = {
    v["video"]["name"].split("_")[0]: v["video"]["player_embed_url"] for v in vimeo_json["folder"]
}
for pretalx_id, video_link in pretalx_to_vimeo.items():
    print(f"pretalx_id: {pretalx_id}, video_link: {video_link}")
    talk = Talk.objects.filter(pretalx_link__contains=pretalx_id).first()
    if talk:
        print(f"Updating talk: {talk.title} with video link: {video_link}")
        talk.video_link = video_link
        talk.video_start_time = 0
        talk.save()
    else:
        print(f"========== Talk with pretalx_id {pretalx_id} not found. ===================")

print("Video links updated successfully.")
