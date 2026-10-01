import os

import requests
from dotenv import load_dotenv
from datetime import datetime, timezone

# Load variables from the .env file
# This allows us to keep sensitive information like the Canvas token
# outside of our Python source code.
load_dotenv()

# Read the Canvas token and Canvas website URL from .env
CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")
CANVAS_URL = os.getenv("CANVAS_URL")

# This header is sent with every Canvas API request.
# The token proves to Canvas that we are authorized to access
# the student's Canvas data.
headers = {
    "Authorization": f"Bearer {CANVAS_TOKEN}"
}


# ---------------------------------------------------------
# GET COURSES
# ---------------------------------------------------------
def get_courses():
    # Canvas API endpoint for getting the user's courses
    url = f"{CANVAS_URL}/api/v1/courses"
    
    # Send a GET request to Canvas.
    # headers contains our authorization token.
    response = requests.get(url, headers=headers)
    
    # If Canvas returns an error such as 401, 404, etc.,
    # Python will raise an exception instead of continuing.
    response.raise_for_status()
    
    # Convert the JSON response from Canvas into Python data
    # and return it.
    return response.json()



# ---------------------------------------------------------
# GET ASSIGNMENTS FOR ONE COURSE
# ---------------------------------------------------------
def get_assignments(course_id):
    
    # course_id tells Canvas which course we want assignments from.
    #
    # Example:
    # course_id = 278577
    #
    # URL becomes:
    # /api/v1/courses/278577/assignments
    url = f"{CANVAS_URL}/api/v1/courses/{course_id}/assignments"
    
    # Extra options that we send with the API request.
    params = {
        # Ask Canvas to also include the student's
        # submission information for each assignment.
        "include[]": "submission",
        # Canvas normally returns results in pages.
        # Asking for 100 prevents us from only getting
        # the first 10 assignments.
        "per_page": 100
    }
    
    
    # Send the GET request with:
    # 1. the API URL
    # 2. our authorization token
    # 3. the extra parameters above
    response = requests.get(
        url,
        headers=headers,
        params=params
    )
    
    
    # Stop if Canvas returned an error.
    response.raise_for_status()
    # Return all assignment data from Canvas
    return response.json()


# ---------------------------------------------------------
# CLEAN THE ASSIGNMENT DATA
# ---------------------------------------------------------
def get_clean_assignments(course_id):
    # First get the raw assignment data from Canvas.
    assignments = get_assignments(course_id)
 
    # This list will contain the simpler EasyCanvas version
    # of each assignment.
    clean_assignments = []
    
    # Go through every assignment returned by Canvas.
    for assignment in assignments:
        # Get the student's submission information.
        # If Canvas does not return submission data,
        # use an empty dictionary instead.
        submission = assignment.get("submission") or {}
        
        
        # Canvas gives us many fields that EasyCanvas does not need.
        # We create our own smaller assignment object containing
        # only the useful information.
        clean_assignment = {
            "id": assignment.get("id"),
            "name": assignment.get("name"),
            "due_at": assignment.get("due_at"),
            "points_possible": assignment.get("points_possible"),
            
            # These two values come from the submission object.
            "submitted_at": submission.get("submitted_at"),
            "status": submission.get("workflow_state")
        }
        # Add the cleaned assignment to our list.
        clean_assignments.append(clean_assignment)
    # Sort assignments by due date.
    #
    # lambda tells Python to use "due_at" as the value
    # used for sorting.
    #
    # If an assignment has no due date, "9999" puts it
    # near the end of the list.
    clean_assignments.sort(
      key=lambda assignment: assignment["due_at"] or "9999"
        )
        

    return clean_assignments


# ---------------------------------------------------------
# GET ONLY UPCOMING ASSIGNMENTS
# ---------------------------------------------------------
def get_upcoming_assignments(course_id):
    # Start with our cleaned and sorted assignments.
    assignments = get_clean_assignments(course_id)
    
    # This list will contain only assignments that
    # the student still needs to complete.
    upcoming = []
    
    # Get the current date and time in UTC.
    #
    # Canvas also gives its API dates in UTC,
    # so we can safely compare them.
    now = datetime.now(timezone.utc)
    
    # Check every assignment.
    for assignment in assignments:
        
        # If the assignment is already submitted or graded,
        # skip it and move to the next assignment.
        if assignment["status"] != "unsubmitted":
            continue
        # If the assignment has no due date,
        # do not include it in the upcoming list.
        if assignment["due_at"] is None:
            continue
        
        # Canvas gives the due date as text such as:
        #
        # 2026-10-06T15:00:00Z
        #
        # Convert that text into a Python datetime object
        # so we can compare it with the current time.
        due_date = datetime.fromisoformat(
            assignment["due_at"].replace("Z", "+00:00")
        )
        
        
        # Only keep the assignment if its due date
        # is still in the future.
        if due_date > now:
            upcoming.append(assignment)

    return upcoming