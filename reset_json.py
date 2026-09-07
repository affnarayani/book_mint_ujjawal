import json
import os

FILE_PATH = "ebook_status.json"

def reset_ebook_status():
    # Check if the JSON file exists
    if not os.path.exists(FILE_PATH):
        print(f"Error: {FILE_PATH} not found.")
        return

    # Load data from the JSON file
    with open(FILE_PATH, "r") as file:
        data = json.load(file)

    # Combine the list of dictionaries into a single lookup mapping
    status_map = {}
    for item in data:
        status_map.update(item)

    # Keys required for the strict AND condition
    required_keys = [
        "ebook_downloaded",
        "cover_generated",
        "kdp_uploaded",
        "gumroad_uploaded"
    ]

    # Verify if all 4 target flags are explicitly set to True
    all_true = all(status_map.get(key) is True for key in required_keys)

    if all_true:
        # Reset target flags to False and clear claude_url and title
        for item in data:
            for key in item:
                if key in required_keys:
                    item[key] = False
                elif key in ["claude_url", "title"]:
                    item[key] = ""

        # Save the updated data back to the file
        with open(FILE_PATH, "w") as file:
            json.dump(data, file, indent=2)

        print("All flags were True. Status, URL, and Title successfully reset!")
    else:
        print("Condition not met (at least one flag is False). No changes applied.")

if __name__ == "__main__":
    reset_ebook_status()