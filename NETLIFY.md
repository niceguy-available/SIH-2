# Deploying on Netlify

Netlify hosts the **website**. It cannot run the Python image-matching
backend, which needs OpenCV and a database, so the backend goes on **Render**
and the database on **MongoDB Atlas**. All three have free tiers.

```
Browser ──> Netlify (website) ──API calls──> Render (Python backend) ──> MongoDB Atlas
```

Do the steps in this order: each one gives you a value the next one needs.

## 1. Database: MongoDB Atlas (about 5 minutes)

1. Sign up at https://www.mongodb.com/cloud/atlas/register and create a **free (M0)** cluster.
2. **Database Access** > Add New Database User: choose a username and password and save them.
3. **Network Access** > Add IP Address > **Allow access from anywhere** (`0.0.0.0/0`).
   Render's free tier has no fixed IP address, so this is required.
4. **Database** > **Connect** > **Drivers**. Copy the connection string and put your
   password in it:
   `mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority`

## 2. Backend: Render (about 10 minutes)

1. Sign up at https://render.com with your GitHub account.
2. **New** > **Blueprint** > select the `SIH-2` repository. Render reads `render.yaml`.
3. When it asks for values:
   - `MONGO_URL`: the Atlas connection string from step 1.
   - `CORS_ORIGINS`: enter `https://example.com` for now. You'll replace it in step 4.
4. Click **Apply**. The first build takes about 10 minutes.
5. When it shows **Live**, copy the service URL, e.g. `https://moon-match-points-api.onrender.com`.
   Opening `<that URL>/api/` should show `{"service":"moon-match-points",...}`.

## 3. Website: Netlify (about 5 minutes)

1. Sign up at https://app.netlify.com with GitHub.
2. **Add new project** > **Import an existing project** > GitHub > `SIH-2`.
   Leave the build settings as they are: they come from `netlify.toml`.
3. Before deploying, add an environment variable:
   - Key: `REACT_APP_BACKEND_URL`
   - Value: your Render URL from step 2, with **no trailing slash**
     (e.g. `https://moon-match-points-api.onrender.com`).
4. **Deploy**. When it finishes, copy your site URL, e.g. `https://moon-match-points.netlify.app`.

If you change `REACT_APP_BACKEND_URL` later, redeploy: **Deploys** >
**Trigger deploy** > **Clear cache and deploy site**. The value is built into the site.

## 4. Connect the two

In Render > your service > **Environment**, set `CORS_ORIGINS` to your Netlify
URL exactly, e.g. `https://moon-match-points.netlify.app` (no trailing slash),
then **Save changes**. Render restarts the backend.

Open the Netlify URL and upload a source image. Done.

## Good to know

- **The first request after idle is slow.** Render's free backend sleeps after
  about 15 minutes without traffic and takes about a minute to wake up. Open the
  site a minute before a demo, or use a paid Render instance.
- **The first upload is slower.** It downloads the curated LROC reference images
  once and caches them in the database.
- **Storage limit.** Uploaded images and results are stored in MongoDB (GridFS).
  Atlas free gives 512 MB, so delete old runs from **Run history** now and then.
- **No login.** Anyone with the link can use the site. Share it deliberately.
- **Custom domain.** Add it in Netlify > Domain management, then add the new
  address to `CORS_ORIGINS` on Render, comma-separated.
