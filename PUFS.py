#!/usr/bin/env python

from __future__ import with_statement

import os

import sys

import errno
import threading 
import subprocess
import json

import zipfile

from threading import Thread


import shutil

import requests 

from scraper import DescriptionScraper

import asyncio


from fuse import FUSE, FuseOSError, Operations, fuse_get_context

#git
import git 


# Google Drive API imports
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import pickle
from googleapiclient.http import MediaFileUpload



# Establishes and returns a Google Drive service object to interact with the Google Drive API.

def google_drive_service():
    
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    # If credentials are not available or they are no longer valid
    if not creds or not creds.valid:
        # Check if the existing credentials can be refreshed (i.e., they have expired but can be renewed)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh access token: {e}")
                # Create a flow instance from the client secrets file to guide the user through the OAuth 2.0 process
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', scopes=['https://www.googleapis.com/auth/drive'])
                creds = flow.run_local_server(port=0)
        else:
            # If no valid refresh token is available, start the OAuth 2.0 flow afresh
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', scopes=['https://www.googleapis.com/auth/drive'])
            creds = flow.run_local_server(port=0)
        
        # Save the obtained or refreshed credentials back to the token.pickle file for future runs
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    # Build the Google Drive service object with the obtained credentials
    service = build('drive', 'v3', credentials=creds)
    return service

class Passthrough(Operations):
    '''
    Initializes a new instance of the Passthrough class, which extends the FUSE Operations class to create
    a custom file system.

    This custom file system integrates with both Git for version control and Google Drive for cloud storage,
    creating a robust environment for managing file operations.
    '''
    def __init__(self, root):
        self.root = root #Stores the root directory for the filesystem operations.
        self.drive_service = google_drive_service() #Google Drive service object
        self.repo = self.init_repository(root) #Git repository object
        self.command_file = '/.commands' #Path to a special command file used to process custom filesystem commands.
        #A dictionary to manage timers for files, used to handle automatic commits after modifications.
        self.file_timers = {} 
        self.crsDescriptions = {} #Stores scraped course descriptions
        self.file_path_to_google_id = {} #Maps local file paths to their corresponding Google Drive file IDs
        self.paths_of_course_folders = set() #Keeps track of directories that have been identified as course-specific
        self.sorted_files = set() #A set of files that have been sorted into their appropriate course folder
        self.sorted_folders = set() #Similar to sorted_files but tracks folders instead.
        #Tracks folders that are identified to be sorted but the sorting action has not yet been completed.
        self.pending_sort_folders = set()

    # Helpers
    # =======

    '''
    Initializes a git repository at the specified root directory for version control over the filesystem. If a git
    repository already exists at the specified location, it simply opens and uses the existing repository.
    '''
    def init_repository(self, root):
        if not os.path.exists(os.path.join(root, '.git')):
            repo = git.Repo.init(root)
            print("Initialized empty Git repository in %s" % repo.git_dir)
        else:
            repo = git.Repo(root)
        return repo
    
    # Converts a relative path from the filesystem root to an absolute path on the host system.
    def _full_path(self, partial):
        if partial.startswith("/"):
            partial = partial[1:]
        path = os.path.join(self.root, partial)
        return path

    # Determines and returns the root-level directory name from a given absolute path.
    def get_root_level_directory(self, full_path):
        # Ensure both paths are absolute and normalized
        base_path = os.path.normpath(self.root)
        full_path = os.path.normpath(full_path)
        
        # Check if full_path actually starts with the base_path
        if not full_path.startswith(base_path):
            raise ValueError("The full path is not under the base path.")
        
        # Strip base_path from full_path and split the remainder
        relative_path = os.path.relpath(full_path, base_path)
        parts = relative_path.split(os.sep)
        
        # The first element in parts, if exists, is the root-level directory
        return parts[0] if parts else None

    '''
    Resets or starts a timer for a file specified by `path`. If a timer for the file already exists, it is canceled
    and a new timer is started. When the timer expires, it triggers a commit of the file changes to the Git repository.
    '''
    def reset_file_timer(self, path):
        if path in self.file_timers:
            self.file_timers[path].cancel()  # Cancel existing timer if it exists
        timer = threading.Timer(10, self.commit_changes, [path, "Updated file: %s" % path ])
        self.file_timers[path] = timer
        timer.start()
    
    '''
    Commits changes made to a file to the Git repository. This function is typically called automatically by a timer
    set after modifying a file.
    '''
    def commit_changes(self, path, message):
        try:
            self.repo.git.add([path])
            new_commit = self.repo.index.commit(message)
            commit_hash = new_commit.hexsha  # Get the hash of the newly created commit
            print(f"Committed changes due to 30s inactivity: {message}, Commit Hash: {commit_hash}")
        finally:
            if path in self.file_timers:
                self.file_timers[path].cancel()
                del self.file_timers[path]

    '''
    Reverts a file to a specific Git commit. This function can be used to undo changes by restoring the file's state
    to that of a specified commit.
    '''
    def revert_to_commit(self, filepath, commit_hash):
        full_path = self._full_path(filepath)
        try:
            self.repo.git.checkout(commit_hash, full_path)
            print(f"Reverted {filepath} to commit {commit_hash}")
            return 1
        except Exception as e:
            print(f"Failed to revert {filepath} to commit {commit_hash}: {e}")
            return 0

   #Initiates a separate thread to run an asynchronous scraping script
    def make(self, folder_name, path):
        def run_script():
            try:
                asyncio.run(self.run_selenium_script(folder_name, path))
            except Exception as e:
                print(f"An error occurred with {folder_name}:", e)

        thread = threading.Thread(target=run_script)
        thread.start()

    #Initiates a seperate thread to run an asynchronous gpt classification api call 
    def classify_and_move_file(self, content, path):

        def run_script():
            try:
                asyncio.run(self.call_gpt4_api(content, path))
            except Exception as e:
                print(f"An error occurred while sorting file {path}: {e}")

        thread = threading.Thread(target=run_script)
        thread.start()
    
    #Aggregates the contents of all text files in a given folder and its subfolders into a single string.
    def aggregate_folder_content(self, folder_path):
        absolute_path = os.path.join(self.root, folder_path)
        content = ""
        for dirpath, dirnames, filenames in os.walk(absolute_path):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                if os.path.exists(file_path) and not filename.startswith('.'):
                    with open(file_path, 'r') as file:
                        content += file.read()
        return content
    
    #Sorts all existing files and folders in the root directory 
    def sort_files(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) not in self.paths_of_course_folders and not d.startswith('.') and not d.startswith('#')]
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                print("sorting the following", full_path)
                if not filename.startswith('.') and not filename.startswith("#") and os.path.exists(full_path):
                    if zipfile.is_zipfile(full_path):
                        self.unzip_file(full_path)
                        continue
                    with open(full_path, 'r') as file:
                            content = file.read()
                    self.classify_and_move_file(content, filename)

            for dirname in dirnames:
                aggreated_content = self.aggregate_folder_content(dirname)
                self.classify_and_move_file(aggreated_content, dirname)
                
            break

    '''
    Processes a text command inputted to the filesystem through a special command file. Commands can
    include 'revert', 'sort', and 'add', each performing different actions like reverting file changes, sorting
    files, or adding new content to the drive.
    '''
    def process_command(self, command):
        try:
            commands = command.strip().split()
            if commands[0].lower() == 'revert' and len(commands) == 3:
                filepath, commit_hash = commands[1], commands[2]
                return self.revert_to_commit(filepath, commit_hash)
            elif commands[0].lower() == 'sort':
                print("Sorting files...")
                self.sort_files()
                return 1
            elif commands[0].lower() == 'add':
                for folder in commands[1:]:
                    self.make(folder, folder)
                return 1
            return -1 
        except Exception as e:
            print(f"Error processing command: {e}")
            return 0  

    ''' 
    Creates a new folder in Google Drive under the specified parent folder. This method helps maintain the
    filesystem's folder structure in the cloud.
    '''
    def create_drive_folder(self, service, folder_path, parent_id=None):
        name = os.path.basename(folder_path)
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        file = service.files().create(body=file_metadata, fields='id').execute()
        folder_id = file.get('id')
        self.file_path_to_google_id[folder_path] = folder_id
        return folder_id
    
    '''
    Uploads a file to Google Drive into the specified folder. This function is used for syncing local files
    to the cloud, allowing for backup and remote access.
    '''
    def add_file_to_drive(self, folder_id, file_path):
        name = os.path.basename(file_path)
        file_metadata = {
            'name': name,
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, mimetype='application/octet-stream', resumable=True)
        file = self.drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        file_id = file.get('id')
        self.file_path_to_google_id[file_path] = file_id
        print(f"Uploaded file to Google Drive with ID: {file['id']}")

    # Recursively add files and subfolders to Google Drive
    def add_folder_to_drive(self, folder_id, folder_path):
        print("Adding folder to Drive: ", folder_path)
        new_folder_id = self.create_drive_folder(self.drive_service, folder_path, folder_id)
        for item in os.listdir(folder_path):
            item_full_path = os.path.join(folder_path, item)
            if os.path.isdir(item_full_path):
                self.add_folder_to_drive(new_folder_id, item_full_path)
            else:
                self.add_file_to_drive(new_folder_id, item_full_path)

    '''
    Updates an existing file on Google Drive. This method is used when a file has been modified locally and
    needs to be synced to the cloud.
    '''
    def update_file_in_drive(self, file_path, file_id):
        file_metadata = {'name': os.path.basename(file_path)}
        media = MediaFileUpload(file_path, resumable=True)
        updated_file = self.drive_service.files().update(
            fileId=file_id,
            body=file_metadata,
            media_body=media).execute()
        print(f"Updated file on Google Drive: {updated_file['id']}")


    ''' 
    Asynchronously calls the GPT-4 API to classify file content, determining which course or category the content
    belongs to based on a set of predefined course descriptions. This method integrates machine learning model
    predictions to automate file organization.
    '''
    async def call_gpt4_api(self, file_content, path):
        # Serialize the current course descriptions into a JSON string for inclusion in the API request.
        crsDescriptions_json = json.dumps(self.crsDescriptions)

        # Define the API URL for the OpenAI GPT-4 completions endpoint.
        api_url = "https://api.openai.com/v1/chat/completions"

        # Setup the request headers, including content type and authorization with an API key.
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-proj-IuaJ6eEdiWQT3RGUhXJXT3BlbkFJuZAKU42gokXUs9hfmApl"
        }

        # Construct the prompt to send to GPT-4, asking it to determine the course code based on file content and available course descriptions.
        prompt = f"Given the file content: {file_content}\n\nIdentify which course this content belongs to after analyzing the following course descriptions and websites: {crsDescriptions_json}\n\nRespond with only the course code. If the content does not clearly belong to any of these courses, or if it is not possible to make a definitive determination, respond with 'unknown'"

        # Set up the data payload for the API request, specifying the model and the conversation context.
        data = {
            "model": "gpt-4-turbo-2024-04-09",
            "messages": [{"role": "system", "content": "You need to determine which course the following content belongs to. Return the course code or 'unknown'"}, {"role": "user", "content": prompt}],
            "max_tokens": 10
        }

        # Perform the API call asynchronously and wait for the response.
        response = requests.post(api_url, headers=headers, json=data)
        response_data = response.json()

        try:

            # Extract the classification result from the API response.
            prediction = response_data['choices'][0]['message']['content']

            # Resolve the full path for the file based on the filesystem root and given relative path.
            full_path = self._full_path(path)

            # Handle the response if the prediction indicates the content does not belong to a known course.
            if prediction.lower() == 'unknown':
                # Optionally, create a new folder in Google Drive for unclassified content.
                self.create_drive_folder(self.drive_service, full_path)
                return 

            # Determine the target directory based on the course code returned by the API.
            target_dir = self._full_path(prediction)
            new_full_path = os.path.join(target_dir, os.path.basename(full_path))

            # Check if the path corresponds to a directory.
            isDir = os.path.isdir(full_path)

            # Move the file or directory to the newly determined target directory.
            shutil.move(full_path, target_dir)
            if os.path.exists(full_path):
                shutil.rmtree(full_path)
            print(f"File moved from {full_path} to {target_dir}")

            # Retrieve the folder ID for the new location from internal mappings.
            folder_id = self.file_path_to_google_id[target_dir]

            # Update Google Drive and internal states based on whether the moved entity is a folder or a file.
            if isDir:
                self.sorted_folders.add(path)
                self.add_folder_to_drive(folder_id, new_full_path)
                print("added folder to drive")
            else:
                self.sorted_files.add(full_path)
                self.add_file_to_drive(folder_id, new_full_path)
                print("added file to drive")
            return prediction
            
        except KeyError:
            # Handle cases where the API response does not include the expected data.
            return "Error in response from GPT-4 API"


    #Asynchronously runs a Selenium script to scrape web data related to a keyword.
    async def run_selenium_script(self, keyword, path):

        print("running script with keyword:", keyword)
    # Execute the external scraper script asynchronously, capturing stdout and stderr
        process = await asyncio.create_subprocess_exec(
            'python3', 'scraper.py', keyword,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        stdout, stderr = await process.communicate()

        # Check the return code of the subprocess to determine if it was successful
        if process.returncode == 0:
            # Parse the JSON output from the scraper script
            result = json.loads(stdout.decode('utf-8'))
            if 'description' in result and result['description']:
                # Store the scraped data in the class attribute for course descriptions
                self.crsDescriptions[keyword] = {
                    'description': result['description'],
                    'sample reading list': result['sample_reading_list'],
                    'course website': result['website']
                }
                # Convert the provided path to a full path within the filesystem
                full_path = self._full_path(path)
                # Update internal sets that track course-related folders
                self.paths_of_course_folders.add(full_path)
                self.sorted_folders.add(keyword)
                # Optionally create a folder on Google Drive to synchronize this content
                self.create_drive_folder(self.drive_service, full_path)
            else:
                # Log any errors returned by the scraper script
                print("Error:", result['error'])
        else:
            # If the scraper script failed, raise an exception with the stderr output for troubleshooting
            raise Exception("Selenium script failed:", stderr.decode('utf-8'))

    #classify and sort depending on file vs folder
    def move_new_file(self, path, full_path):
        parent_dir = os.path.dirname(full_path)
        
        if parent_dir != self.root:
            root_level_dir = self.get_root_level_directory(full_path)
            
        #if the root level directory of the file is self.root, the file is in the root directory and we can immediately classify and sort it
        if parent_dir == self.root and full_path not in self.sorted_files:
            with open(full_path, 'r') as file:
                content = file.read()
            self.classify_and_move_file(content, path)

        #we have a new folder that we need to classify and sort
        elif parent_dir != self.root and root_level_dir not in self.sorted_folders and root_level_dir not in self.pending_sort_folders:
            self.pending_sort_folders.add(root_level_dir)
            aggregated_content = self.aggregate_folder_content(root_level_dir)
            self.classify_and_move_file(aggregated_content, root_level_dir)

    #Extracts the contents of a zip file to the filesystem root directory. 
    def unzip_file(self, file_path):
        path = ""
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            all_items = zip_ref.namelist()
            for item in all_items:
                split_path = item.split("/")
                if len(split_path) == 2 and '' in split_path:
                    path = item
                    break
            zip_ref.extractall(self.root)

        if path:
            full_path = self._full_path(path)
            self.move_new_file(path, full_path)
            # Delete the zip file after extraction
            os.remove(file_path)

    # Filesystem methods
    # ==================

    def access(self, path, mode):
        full_path = self._full_path(path)
        if not os.access(full_path, mode):
            raise FuseOSError(errno.EACCES)

    def chmod(self, path, mode):
        full_path = self._full_path(path)
        return os.chmod(full_path, mode)

    def chown(self, path, uid, gid):
        full_path = self._full_path(path)
        return os.chown(full_path, uid, gid)

    def getattr(self, path, fh=None):
        full_path = self._full_path(path)
        st = os.lstat(full_path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
                     'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    def readdir(self, path, fh):
        full_path = self._full_path(path)

        dirents = ['.', '..']
        if os.path.isdir(full_path):
            dirents.extend(os.listdir(full_path))
        for r in dirents:
            yield r

    def readlink(self, path):
        pathname = os.readlink(self._full_path(path))
        if pathname.startswith("/"):
            return os.path.relpath(pathname, self.root)
        else:
            return pathname

    def mknod(self, path, mode, dev):
        return os.mknod(self._full_path(path), mode, dev)

    def rmdir(self, path):
        full_path = self._full_path(path)
        return os.rmdir(full_path)

    def mkdir(self, path, mode):

        folder_name = os.path.basename(path)

        full_path = self._full_path(path)

        root_level_dir = self.get_root_level_directory(full_path)
        if root_level_dir not in self.sorted_folders:
            self.make(folder_name, path)

        return os.mkdir(self._full_path(path), mode)

    def statfs(self, path):
        full_path = self._full_path(path)
        stv = os.statvfs(full_path)
        return dict((key, getattr(stv, key)) for key in ('f_bavail', 'f_bfree',
            'f_blocks', 'f_bsize', 'f_favail', 'f_ffree', 'f_files', 'f_flag',
            'f_frsize', 'f_namemax'))

    def unlink(self, path):
        return os.unlink(self._full_path(path))

    def symlink(self, name, target):
        return os.symlink(target, self._full_path(name))

    def rename(self, old, new):
        result = os.rename(self._full_path(old), self._full_path(new))
        print("Renamed from %s to %s" % (old, new))
        #self.commit_changes("Renamed from %s to %s" % (old, new))
       
        return result

    def link(self, target, name):
        return os.link(self._full_path(name), self._full_path(target))

    def utimens(self, path, times=None):
        return os.utime(self._full_path(path), times)

    # File methods
    # ============

    def open(self, path, flags):
        full_path = self._full_path(path)
        return os.open(full_path, flags)

    def create(self, path, mode, fi=None):
        uid, gid, pid = fuse_get_context()
        full_path = self._full_path(path)
        fd = os.open(full_path, os.O_WRONLY | os.O_CREAT, mode)
        os.chown(full_path,uid,gid) #chown to context uid & gid
    

        return fd

    def read(self, path, length, offset, fh):
        os.lseek(fh, offset, os.SEEK_SET)
        return os.read(fh, length)

    def write(self, path, buf, offset, fh):

        if path == self.command_file:
            # Process command written to the command file. If command processing returns True, return buffer length; otherwise, -1.
            return len(buf) if self.process_command(buf.decode()) else -1
        else:
            # Position the file pointer to the offset for writing data
            os.lseek(fh, offset, os.SEEK_SET)
            # Write data to the file and capture the result (number of bytes written)
            result = os.write(fh, buf)
            # Get the absolute path of the file to check various conditions
            full_path = self._full_path(path)
            filename = os.path.basename(path)

            # Skip further processing for files that should not trigger synchronization or other operations
            if filename.startswith('.') or filename.startswith('#') or filename.endswith('.zip') or os.path.getsize(full_path) == 0:
                return len(buf)

            # Identify the root level directory to determine if it's part of sorted directories
            root_level_dir = self.get_root_level_directory(full_path)
            if root_level_dir in self.sorted_folders:
                # If the file is in Google Drive's synchronized list, handle its synchronization
                if full_path in self.file_path_to_google_id:
                    self.reset_file_timer(full_path)  # Reset the timer to delay the commit due to ongoing changes
                    file_id = self.file_path_to_google_id[full_path]
                    self.update_file_in_drive(full_path, file_id)  # Update the file in Google Drive
            else:
                # If not sorted, attempt to move and classify the file
                self.move_new_file(path, full_path)

            # Return the number of bytes written
            return result
        
    def truncate(self, path, length, fh=None):
        full_path = self._full_path(path)
        with open(full_path, 'r+') as f:
            f.truncate(length)

    def flush(self, path, fh):
        return os.fsync(fh)

    def release(self, path, fh):
        full_path = self._full_path(path)
        #if the unzipped file does not already exist in the root directory, we need to unzip it using unzip_file
        if path.endswith('.zip') and not path.startswith('/.') and not os.path.exists(self._full_path(path[:-4])) and zipfile.is_zipfile(full_path) and  os.path.getsize(full_path) > 0:
            print("unzipping file", path)
            self.unzip_file(full_path)
        return os.close(fh)
    
    def close(self, path, fh):
        os.close(fh)
        return 0  # Return 0 to indicate success

    def fsync(self, path, fdatasync, fh):
        return self.flush(path, fh)


def main(mountpoint, root):
    fuse_instance = Passthrough(root)
    FUSE(fuse_instance, mountpoint, nothreads=True, foreground=True, allow_other=True)

if __name__ == '__main__':
    main(sys.argv[2], sys.argv[1])