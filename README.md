# MoodFlow 🎵🎬

MoodFlow is a mood-based entertainment recommendation web application that provides personalized movie, TV show, music, and activity suggestions based on the user’s mood and preferences.

The application interacts with users through a mood-selection flow and generates recommendations using the TMDb API and Spotify API.

---

## ✨ Features

- User login and authentication
- Mood-based recommendation system
- Stay-in or go-out recommendation flow
- Movie recommendations using TMDb API
- TV show recommendations
- Spotify playlist recommendations
- Activity suggestions based on mood
- Save recommendations for later
- Redirects to movie details and playlists
- Interactive and user-friendly interface

---

## 🛠️ Tech Stack

### Frontend
- HTML
- CSS
- JavaScript

### Backend
- Python
- Flask

### Database
- SQLite

### APIs Used
- TMDb API
- Spotify API

---

## 🔄 Project Workflow

1. User logs into the application
2. User selects their current mood
3. User chooses whether they want to:
   - Stay In
   - Go Out
4. Based on the selection, the app recommends:
   - Movies
   - TV Shows
   - Music playlists
   - Activities
5. Users can save recommendations and revisit them later

---

## 🚀 Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/moodflow.git
```

Move into the project directory:

```bash
cd moodflow
```

Create a virtual environment:

```bash
python -m venv venv
```

Activate the virtual environment:

### macOS/Linux
```bash
source venv/bin/activate
```

### Windows
```bash
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the Flask application:

```bash
python app.py
```

---

## 🔐 Environment Variables

Create a `.env` file in the root directory and add:

```env
TMDB_API_KEY=your_tmdb_api_key
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
```

⚠️ Do not upload your `.env` file or API keys publicly.

---

## 📁 Project Structure

```text
moodflow/
│
├── static/
├── templates/
├── instance/
├── app.py
├── requirements.txt
├── README.md
└── .env
```

---

## 📌 Future Improvements

- AI-powered recommendations
- Better mood analysis
- Improved personalization
- Mobile responsiveness
- Dark/Light mode
- Recommendation history tracking

---

## 👩‍💻 Author

Rachel Jessica

---

## ⭐ Acknowledgements

- TMDb API
- Spotify API
- Flask Documentation
- Open-source developer community
