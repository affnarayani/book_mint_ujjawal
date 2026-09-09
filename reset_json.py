import json
import os
import re
import shutil

STATUS_FILE_PATH = "ebook_status.json"
IDEAS_FILE_PATH = "ebook_ideas.json"
EBOOK_DIR = "ebook"
PUBLISHED_DIR = "published"

def get_clean_folder_name(title):
    # 1. Non-alphanumeric characters ko space se replace karo (words/numbers alag rahein)
    clean_str = re.sub(r'[^\w\s]', ' ', title)
    # 2. Spaces ya multiple underscores ko single underscore se replace karo
    clean_str = re.sub(r'[\s_]+', '_', clean_str)
    # 3. Lowercase mein convert karo aur surrounding underscores strip karo
    clean_str = clean_str.strip('_').lower()
    
    return clean_str if clean_str else "untitled_ebook"

def get_unique_folder_path(base_dir, folder_name):
    target_path = os.path.join(base_dir, folder_name)
    if not os.path.exists(target_path):
        return target_path

    i = 2
    while True:
        new_folder_name = f"{folder_name}_{i}"
        new_target_path = os.path.join(base_dir, new_folder_name)
        if not os.path.exists(new_target_path):
            return new_target_path
        i += 1

def move_ebook_contents(dest_folder):
    if not os.path.exists(EBOOK_DIR):
        print(f"Warning: Source directory '{EBOOK_DIR}' does not exist. No files moved.")
        return

    os.makedirs(dest_folder, exist_ok=True)

    for item in os.listdir(EBOOK_DIR):
        src_path = os.path.join(EBOOK_DIR, item)
        dest_path = os.path.join(dest_folder, item)
        shutil.move(src_path, dest_path)
    
    print(f"Successfully moved all contents from '{EBOOK_DIR}' to '{dest_folder}'.")

def mark_idea_as_published(title_to_match):
    if not os.path.exists(IDEAS_FILE_PATH):
        print(f"Warning: {IDEAS_FILE_PATH} not found. Skipping ideas update.")
        return

    with open(IDEAS_FILE_PATH, "r") as file:
        ideas_data = json.load(file)

    updated = False

    # Target dictionary key ke andar hi "ebook_published": True set karo
    for item in ideas_data:
        if item.get("title") == title_to_match:
            item["ebook_published"] = True
            updated = True
            break

    if updated:
        with open(IDEAS_FILE_PATH, "w") as file:
            json.dump(ideas_data, file, indent=2)
        print(f"Updated '{IDEAS_FILE_PATH}': Set 'ebook_published': true inside object for '{title_to_match}'.")
    else:
        print(f"Note: Title '{title_to_match}' not found in {IDEAS_FILE_PATH}.")

def reset_ebook_status():
    if not os.path.exists(STATUS_FILE_PATH):
        print(f"Error: {STATUS_FILE_PATH} not found.")
        return

    with open(STATUS_FILE_PATH, "r") as file:
        data = json.load(file)

    status_map = {}
    for item in data:
        status_map.update(item)

    required_keys = [
        "ebook_downloaded",
        "details_generated",
        "cover_generated",
        "banner_generated",
        "thumbnail_generated",
        "kdp_uploaded",
        "gumroad_uploaded"
    ]

    # Verify if all 7 target flags are True
    all_true = all(status_map.get(key) is True for key in required_keys)

    if all_true:
        raw_title = status_map.get("title", "")
        clean_name = get_clean_folder_name(raw_title)
        
        # Step 1: 'published' directory ke andar unique target folder path generate karo
        os.makedirs(PUBLISHED_DIR, exist_ok=True)
        unique_target_path = get_unique_folder_path(PUBLISHED_DIR, clean_name)

        # Step 2: 'ebook' folder ka content move karo
        move_ebook_contents(unique_target_path)

        # Step 3: 'ebook_ideas.json' mein matching title object ke andar "ebook_published": true add karo
        if raw_title:
            mark_idea_as_published(raw_title)

        # Step 4: Reset target flags to False & clear claude_url and title
        for item in data:
            for key in item:
                if key in required_keys:
                    item[key] = False
                elif key in ["claude_url", "title"]:
                    item[key] = ""

        # Step 5: Updated status JSON save karo
        with open(STATUS_FILE_PATH, "w") as file:
            json.dump(data, file, indent=2)

        print("All 7 flags were True. Content moved, ideas updated, and status JSON successfully reset!")
    else:
        print("Condition not met (at least one flag is False). No actions taken.")

if __name__ == "__main__":
    reset_ebook_status()