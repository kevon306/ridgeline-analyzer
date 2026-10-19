# Put the analyzer on the web (no terminal, no Python)

You need two free accounts: GitHub (stores the files) and Streamlit Community Cloud (runs the app). About 20 minutes.

## A. GitHub
1. Go to github.com, click **Sign up**, and make an account (or sign in).
2. Click the **+** at the top right, then **New repository**.
3. Repository name: `ridgeline-analyzer`. Choose **Public**. Tick **Add a README file**. Click **Create repository**.
4. On your computer, unzip `ridgeline-engine.zip`. Open the folder named `ridgeline`.
5. Back on GitHub, click **Add file**, then **Upload files**.
6. Select **everything inside** the `ridgeline` folder (not the folder itself) and drag it onto the page. Keep the folders: `ridgeline_core`, `tests`, `data`, and `.streamlit`.
   - On a Mac, hidden folders (starting with a dot) are hidden: press **Cmd + Shift + .** in Finder to show them.
   - If GitHub will not take the `.streamlit` folder, skip it. The app still works; only the colors change.
7. Wait for every file to finish uploading. Scroll down, click **Commit changes**.
8. Check the repository page shows `app.py`, `requirements.txt`, and a `data` folder that contains `gwinnett.db.gz`.

## B. Streamlit Community Cloud
1. Go to share.streamlit.io and click **Continue with GitHub**. Allow access when asked.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Repository: `YOUR-USERNAME/ridgeline-analyzer`. Branch: `main`. Main file path: `app.py`.
4. Click **Advanced settings** and set Python version to **3.12**. Click **Save**, then **Deploy**.
5. Wait 2 to 5 minutes while it installs. When it finishes you get a web address ending in `.streamlit.app`. That is your live URL.

## C. Check that it works
1. Open the live URL. Tick **Use the development example instead** in the left sidebar. You should see 20 processed, 14 ranked, 2 eliminated, 2 could not value, 2 could not match.
2. Click **Download a blank template**, type in a few real addresses and asking prices, save it, and upload it back.
3. The first page load after a quiet period takes 10 to 20 seconds while the county records load. That is normal.

## D. Hand it in
- Repository link: the address of your GitHub repository (starts with `https://github.com/`). It is public, so Dr. Marshall can open it.
- Recording: use QuickTime (Mac: Cmd + Shift + 5) or the Windows Game Bar (Windows key + G) to record the live URL with your 20 real listings, about 3 minutes. Upload to YouTube as **Unlisted**, then open the link in a private window to check it plays.
- Live URL: optional. Open it in a private window before you paste it.

## E. Changing something later
On GitHub, open the file, click the pencil icon, edit, and click **Commit changes**. The app updates by itself within a minute.

## If something goes wrong
- *"ModuleNotFoundError"*: `requirements.txt` is not at the top level of the repository. Upload it again.
- *"No such file" for `gwinnett.db.gz`*: the `data` folder was not uploaded. Add it.
- *The app says it is asleep*: click **Yes, get this app back up**. Free apps sleep after a few days without visitors.
- *Everything shows "could not match"*: you are on the small development database. Check `data/gwinnett.db.gz` is in the repository.
