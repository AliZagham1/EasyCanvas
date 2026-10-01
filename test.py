import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("CANVAS_TOKEN")
canvas_url = os.getenv("CANVAS_URL")

headers = {
    "Authorization": f"Bearer {token}"
}

course_id = 278577  # CSE 3311

response = requests.get(
    f"{canvas_url}/api/v1/courses/{course_id}/assignments",
    headers=headers,
    params={
        "include[]": "submission"
    }
)

print("Status:", response.status_code)

if response.status_code == 200:
    print("\nCSE 3311 Assignments:\n")

    for assignment in response.json():
        print("Assignment:", assignment.get("name"))
        print("Due:", assignment.get("due_at"))
        print("Points:", assignment.get("points_possible"))

        submission = assignment.get("submission")

        if submission:
            print("Submitted:", submission.get("submitted_at"))
            print("Status:", submission.get("workflow_state"))
        else:
            print("Submission info: Not available")

        print("-------------------------")

else:
    print("Error:", response.status_code)
    print(response.text)