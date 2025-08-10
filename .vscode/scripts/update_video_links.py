"""Script to update video links in the database from a JSON file containing Vimeo links."""
import json
from pathlib import Path

from talks.models import Talk


vimeo_json_path = Path("vimeo_links.json")
vimeo_json = json.loads(vimeo_json_path.read_text())
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
