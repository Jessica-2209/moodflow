import os, requests, base64, time
from flask import Flask, render_template, redirect, url_for, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY']                  = os.getenv('SECRET_KEY', 'moodflow-dev-secret')
app.config['SQLALCHEMY_DATABASE_URI']     = 'sqlite:///moodflow.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db            = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view    = 'login'
login_manager.login_message = ''

SPOTIFY_CLIENT_ID     = os.getenv('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', '')
TMDB_API_KEY          = os.getenv('TMDB_API_KEY', '')
TMDB_BASE             = 'https://api.themoviedb.org/3'
TMDB_IMG              = 'https://image.tmdb.org/t/p/w342'

# ── MODELS ───────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    saved    = db.relationship('SavedItem', backref='user', lazy=True,
                               cascade='all, delete-orphan')

class SavedItem(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_type    = db.Column(db.String(20))   # movie | tvshow | playlist | activity
    item_id      = db.Column(db.String(100))
    title        = db.Column(db.String(200))
    subtitle     = db.Column(db.String(300))
    image_url    = db.Column(db.String(500))
    external_url = db.Column(db.String(500))
    __table_args__ = (db.UniqueConstraint('user_id', 'item_type', 'item_id'),)

@login_manager.user_loader
def load_user(uid): return User.query.get(int(uid))

# Return JSON 401 for API routes instead of redirecting to login page
def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Not authenticated', 'redirect': '/login'}), 401
        return f(*args, **kwargs)
    return decorated

# ── SPOTIFY TOKEN CACHE ───────────────────────────────────────────────────────

_sp_token        = None
_sp_token_expiry = 0

def get_spotify_token():
    global _sp_token, _sp_token_expiry
    if _sp_token and time.time() < _sp_token_expiry - 60:
        return _sp_token
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    creds = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    try:
        r = requests.post(
            'https://accounts.spotify.com/api/token',
            headers={'Authorization': f'Basic {creds}'},
            data={'grant_type': 'client_credentials'},
            timeout=8)
        if r.status_code == 200:
            d = r.json()
            _sp_token        = d['access_token']
            _sp_token_expiry = time.time() + d['expires_in']
            return _sp_token
    except Exception:
        pass
    return None

# ── MOOD → SPOTIFY QUERIES ────────────────────────────────────────────────────

MOOD_SPOTIFY = {
    'happy':     ['feel good pop hits', 'happy vibes playlist', 'good mood music'],
    'sad':       ['sad songs playlist', 'emotional acoustic', 'heartbreak playlist'],
    'excited':   ['hype songs 2024', 'energy boost playlist', 'pump up songs'],
    'chill':     ['lofi hip hop chill', 'chill vibes playlist', 'calm study music'],
    'romantic':  ['romantic songs playlist', 'love songs', 'jazz romance evening'],
    'anxious':   ['calm anxiety music', 'meditation healing music', 'peaceful piano'],
    'bored':     ['new music discoveries', 'indie gems playlist', 'fresh hits weekly'],
    'energetic': ['workout hits', 'running motivation playlist', 'gym music mix'],
}

def fetch_spotify_playlists(mood, limit=6):
    token = get_spotify_token()
    if not token:
        return []
    queries = MOOD_SPOTIFY.get(mood, [mood + ' playlist'])
    results, seen = [], set()
    for q in queries:
        try:
            r = requests.get(
                'https://api.spotify.com/v1/search',
                headers={'Authorization': f'Bearer {token}'},
                params={'q': q, 'type': 'playlist', 'limit': 4, 'market': 'IN'},
                timeout=8)
            if r.status_code != 200:
                continue
            for pl in (r.json().get('playlists', {}) or {}).get('items', []) or []:
                if not pl or pl['id'] in seen:
                    continue
                seen.add(pl['id'])
                imgs  = pl.get('images') or []
                owner = (pl.get('owner') or {}).get('display_name', 'Spotify')
                total = (pl.get('tracks') or {}).get('total', '?')
                desc  = (pl.get('description') or '').strip()
                # Strip HTML tags from description
                import re
                desc = re.sub(r'<[^>]+>', '', desc)[:120]
                results.append({
                    'id':       pl['id'],
                    'title':    pl['name'],
                    'subtitle': f"{total} tracks · by {owner}",
                    'desc':     desc,
                    'image':    imgs[0]['url'] if imgs else '',
                    'url':      pl['external_urls']['spotify'],
                    'type':     'playlist',
                })
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results[:limit]

# ── TMDB GENRE MAPS ───────────────────────────────────────────────────────────

MOOD_MOVIE_GENRES = {
    'happy':     '35,10751',
    'sad':       '18',
    'excited':   '28,12,53',
    'chill':     '16,35,14',
    'romantic':  '10749,18',
    'anxious':   '99,36',
    'bored':     '878,9648,53',
    'energetic': '28,53,12',
}

MOOD_TV_GENRES = {
    'happy':     '35,10751',
    'sad':       '18',
    'excited':   '10759,80',
    'chill':     '16,35',
    'romantic':  '18',
    'anxious':   '99',
    'bored':     '10765,9648',
    'energetic': '10759,80',
}

def _tmdb_headers():
    """Use Bearer token auth (TMDB v3 Read Access Token) if key looks like a JWT,
    otherwise fall back to api_key query param. Both work with the same key value."""
    return {'Authorization': f'Bearer {TMDB_API_KEY}'} if TMDB_API_KEY else {}

def _movie(m):
    poster   = f"{TMDB_IMG}{m['poster_path']}" if m.get('poster_path') else ''
    rating   = round(m.get('vote_average') or 0, 1)
    votes    = m.get('vote_count') or 0
    year     = (m.get('release_date') or '')[:4]
    overview = (m.get('overview') or '')
    if len(overview) > 160:
        overview = overview[:160] + '…'
    return {
        'id':       str(m['id']),
        'title':    m.get('title') or 'Unknown',
        'subtitle': f"★ {rating}/10  ·  {votes:,} votes  ·  {year}",
        'image':    poster,
        'url':      f"https://www.themoviedb.org/movie/{m['id']}",
        'type':     'movie',
        'rating':   rating,
        'votes':    votes,
        'year':     year,
        'overview': overview,
    }

def _tvshow(s):
    poster   = f"{TMDB_IMG}{s['poster_path']}" if s.get('poster_path') else ''
    rating   = round(s.get('vote_average') or 0, 1)
    votes    = s.get('vote_count') or 0
    year     = (s.get('first_air_date') or '')[:4]
    seasons  = s.get('number_of_seasons') or ''
    seas_str = f"  ·  {seasons} season{'s' if seasons != 1 else ''}" if seasons else ''
    overview = (s.get('overview') or '')
    if len(overview) > 160:
        overview = overview[:160] + '…'
    return {
        'id':       str(s['id']),
        'title':    s.get('name') or 'Unknown',
        'subtitle': f"★ {rating}/10  ·  {votes:,} votes  ·  {year}{seas_str}",
        'image':    poster,
        'url':      f"https://www.themoviedb.org/tv/{s['id']}",
        'type':     'tvshow',
        'rating':   rating,
        'votes':    votes,
        'year':     year,
        'overview': overview,
        'seasons':  seasons,
    }

def fetch_movies(mood, limit=8):
    if not TMDB_API_KEY:
        return []
    try:
        r = requests.get(f'{TMDB_BASE}/discover/movie', params={
            'api_key':          TMDB_API_KEY,   # api_key param (v3)
            'language':         'en-US',
            'sort_by':          'popularity.desc',
            'with_genres':      MOOD_MOVIE_GENRES.get(mood, '35'),
            'vote_count.gte':   100,             # lowered from 300 — more results
            'vote_average.gte': 6.0,             # lowered from 6.5
            'include_adult':    'false',
            'page':             1,
        }, timeout=15)
        if r.status_code == 401:
            print(f'[TMDB] 401 Unauthorised — check your TMDB_API_KEY in .env')
            return []
        if r.status_code != 200:
            print(f'[TMDB] movies HTTP {r.status_code}: {r.text[:200]}')
            return []
        results = r.json().get('results') or []
        print(f'[TMDB] movies ({mood}): {len(results)} raw results')
        return [_movie(m) for m in results[:limit]]
    except Exception as e:
        print(f'[TMDB] fetch_movies exception: {e}')
        return []

def fetch_tvshows(mood, limit=8):
    if not TMDB_API_KEY:
        return []
    try:
        r = requests.get(f'{TMDB_BASE}/discover/tv', params={
            'api_key':          TMDB_API_KEY,
            'language':         'en-US',
            'sort_by':          'popularity.desc',
            'with_genres':      MOOD_TV_GENRES.get(mood, '18'),
            'vote_count.gte':   50,              # lowered from 200
            'vote_average.gte': 6.0,             # lowered from 6.5
            'include_adult':    'false',
            'page':             1,
        }, timeout=15)
        if r.status_code == 401:
            print(f'[TMDB] 401 Unauthorised — check your TMDB_API_KEY in .env')
            return []
        if r.status_code != 200:
            print(f'[TMDB] tvshows HTTP {r.status_code}: {r.text[:200]}')
            return []
        results = r.json().get('results') or []
        print(f'[TMDB] tvshows ({mood}): {len(results)} raw results')
        return [_tvshow(s) for s in results[:limit]]
    except Exception as e:
        print(f'[TMDB] fetch_tvshows exception: {e}')
        return []

# ── ACTIVITIES ────────────────────────────────────────────────────────────────

ACTIVITIES = {
    ('happy','stay_in'):[
        {'id':'h-si-1','title':'Homemade Pizza Night','description':'Pick your toppings and throw a festive pizza party from scratch.','emoji':'🍕','category':'food'},
        {'id':'h-si-2','title':'Jackbox Party Games','description':'Play hilarious party games online with friends or family.','emoji':'🎮','category':'game'},
        {'id':'h-si-3','title':'Bake Something New','description':'Brownies, banana bread, or focaccia — fresh from your oven.','emoji':'🧁','category':'food'},
    ],
    ('happy','go_out'):[
        {'id':'h-go-1','title':'Rooftop Cafe Visit','description':'Find a trendy rooftop cafe with great views and a cold brew.','emoji':'☕','category':'restaurant'},
        {'id':'h-go-2','title':'Street Food Tour','description':'Explore local street food hotspots — chaat, golgappas, and more!','emoji':'🌮','category':'outdoor'},
        {'id':'h-go-3','title':'Cycling in the Park','description':'Rent a cycle and explore a scenic park trail.','emoji':'🚴','category':'outdoor'},
    ],
    ('sad','stay_in'):[
        {'id':'s-si-1','title':'Warm Chai and Pakoras','description':'Brew a strong chai, fry some pakoras, wrap in a blanket.','emoji':'🍵','category':'food'},
        {'id':'s-si-2','title':'Journal Your Thoughts','description':'Get a notebook and pour your heart out — writing is therapy.','emoji':'📓','category':'activity'},
        {'id':'s-si-3','title':'Comfort Cook','description':'Make that one dish that always feels like a warm hug.','emoji':'🍲','category':'food'},
    ],
    ('sad','go_out'):[
        {'id':'s-go-1','title':'Quiet Garden Walk','description':'Find a botanical garden or quiet park. Nature heals.','emoji':'🌸','category':'outdoor'},
        {'id':'s-go-2','title':'Visit an Art Gallery','description':'Surround yourself with beauty. Let the art speak to you.','emoji':'🖼️','category':'activity'},
        {'id':'s-go-3','title':'Comfort Restaurant','description':'Go to that one place that always makes you feel better.','emoji':'🍜','category':'restaurant'},
    ],
    ('excited','stay_in'):[
        {'id':'e-si-1','title':'Start a New Project','description':'Channel that excitement — code an app, start a blog, learn a skill!','emoji':'💡','category':'activity'},
        {'id':'e-si-2','title':'Home HIIT Circuit','description':'Burpees, jump squats, mountain climbers. No equipment needed.','emoji':'💪','category':'activity'},
        {'id':'e-si-3','title':'Complex Cook Challenge','description':'Ramen from scratch, croissants, paella — push your skills.','emoji':'🍳','category':'food'},
    ],
    ('excited','go_out'):[
        {'id':'e-go-1','title':'Rock Climbing','description':'Push your limits, feel the adrenaline, conquer the wall!','emoji':'🧗','category':'activity'},
        {'id':'e-go-2','title':'Dance Class or Zumba','description':'Take your energy to the dance floor — salsa, hip-hop, Zumba!','emoji':'💃','category':'activity'},
        {'id':'e-go-3','title':'Hike a Nearby Trail','description':'Burn that energy on an uphill trail with a rewarding view.','emoji':'🥾','category':'outdoor'},
    ],
    ('chill','stay_in'):[
        {'id':'c-si-1','title':'Cheese Board and Wine','description':'Assemble charcuterie with cheeses, grapes, crackers.','emoji':'🧀','category':'food'},
        {'id':'c-si-2','title':'Sketching or Doodling','description':'No pressure art — just let your pen wander across the page.','emoji':'✏️','category':'activity'},
        {'id':'c-si-3','title':'Read by Candlelight','description':'Pick up that book you have been meaning to start.','emoji':'🕯️','category':'activity'},
    ],
    ('chill','go_out'):[
        {'id':'c-go-1','title':'Sunset by the Water','description':'Find a lake, river, or beach. Watch the sky turn colours.','emoji':'🌅','category':'outdoor'},
        {'id':'c-go-2','title':'Quiet Cafe with a Book','description':'Cosy corner, favourite drink, uninterrupted reading.','emoji':'☕','category':'restaurant'},
        {'id':'c-go-3','title':'Farmers Market Stroll','description':'Browse artisan goods and homemade snacks at your pace.','emoji':'🛒','category':'outdoor'},
    ],
    ('romantic','stay_in'):[
        {'id':'r-si-1','title':'Candlelit Dinner for Two','description':'Pasta, dim lights, candles, good wine. Set the scene.','emoji':'🕯️','category':'food'},
        {'id':'r-si-2','title':'Couples Spa Night','description':'DIY face masks, foot soaks, massages. Pamper each other.','emoji':'🧖','category':'activity'},
        {'id':'r-si-3','title':'Cook a New Recipe Together','description':'Pick a dish neither of you has made. Learn it together.','emoji':'👨‍🍳','category':'food'},
    ],
    ('romantic','go_out'):[
        {'id':'r-go-1','title':'Intimate Fine Dining','description':'Book a cosy restaurant with mood lighting and a tasting menu.','emoji':'🍷','category':'restaurant'},
        {'id':'r-go-2','title':'Stargazing Spot','description':'Drive out of the city, lay on the car hood, count stars.','emoji':'⭐','category':'outdoor'},
        {'id':'r-go-3','title':'Sunset Boat Ride','description':'A gentle cruise at golden hour is pure magic.','emoji':'⛵','category':'outdoor'},
    ],
    ('anxious','stay_in'):[
        {'id':'a-si-1','title':'5-4-3-2-1 Grounding','description':'5 things you see, 4 touch, 3 hear, 2 smell, 1 taste. Right now.','emoji':'🌿','category':'activity'},
        {'id':'a-si-2','title':'Warm Turmeric Latte','description':'Golden milk with oat milk, turmeric, cinnamon. Soothing.','emoji':'☕','category':'food'},
        {'id':'a-si-3','title':'Gentle Stretching Routine','description':'20 minutes of slow stretching. Let your body release tension.','emoji':'🧘','category':'activity'},
    ],
    ('anxious','go_out'):[
        {'id':'a-go-1','title':'Barefoot Walk on Grass','description':'Take your shoes off. Feel the earth. Breathe slowly.','emoji':'🌱','category':'outdoor'},
        {'id':'a-go-2','title':'Gentle Yoga Class','description':'A beginner session resets your nervous system beautifully.','emoji':'🧘','category':'activity'},
        {'id':'a-go-3','title':'Familiar Comfort Cafe','description':'Somewhere known and safe. A warm drink in a familiar space.','emoji':'☕','category':'restaurant'},
    ],
    ('bored','stay_in'):[
        {'id':'b-si-1','title':'Rearrange Your Room','description':'New furniture layout, declutter, redecorate. Fresh perspective.','emoji':'🛋️','category':'activity'},
        {'id':'b-si-2','title':'Learn Something on YouTube','description':'Origami, stock market basics, Italian cooking — pick anything.','emoji':'📺','category':'activity'},
        {'id':'b-si-3','title':'Challenge Cook','description':'Croissants, souffle, ramen from scratch — push your skills.','emoji':'🍳','category':'food'},
    ],
    ('bored','go_out'):[
        {'id':'b-go-1','title':'Explore a New Neighbourhood','description':'Pick a part of the city you have never been to. Just wander.','emoji':'🗺️','category':'outdoor'},
        {'id':'b-go-2','title':'Escape Room','description':'Book one with friends — intense, social, completely engaging.','emoji':'🔐','category':'activity'},
        {'id':'b-go-3','title':'Photography Walk','description':'Take your phone and photograph the ordinary. Make it art.','emoji':'📸','category':'outdoor'},
    ],
    ('energetic','stay_in'):[
        {'id':'en-si-1','title':'Home HIIT Workout','description':'Jump squats, burpees, push-ups — no equipment needed.','emoji':'💪','category':'activity'},
        {'id':'en-si-2','title':'Deep Clean Your Space','description':'Blast music and transform your home with all that energy.','emoji':'🧹','category':'activity'},
        {'id':'en-si-3','title':'Protein Smoothie Bowl','description':'Blend, top with granola, seeds, fresh fruit. Fuel up!','emoji':'🥤','category':'food'},
    ],
    ('energetic','go_out'):[
        {'id':'en-go-1','title':'Badminton or Basketball','description':'Grab friends, book a court, sweat it out competitively!','emoji':'🏸','category':'activity'},
        {'id':'en-go-2','title':'Morning Run in the Park','description':'Lace up and go. Nothing burns energy like an outdoor run.','emoji':'🏃','category':'outdoor'},
        {'id':'en-go-3','title':'Swimming Pool or Aqua Park','description':'Swim laps or go wild. Perfect for high energy days.','emoji':'🏊','category':'activity'},
    ],
}

# ── REST API ──────────────────────────────────────────────────────────────────

@app.route('/api/v1/status')
def api_status():
    return jsonify({
        'spotify': bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET),
        'tmdb':    bool(TMDB_API_KEY),
        'version': '4.0',
    })

@app.route('/api/v1/recommendations')
@api_login_required
def api_recommendations():
    mood = request.args.get('mood', '').lower().strip()
    loc  = request.args.get('location', '').lower().strip()
    if not mood or not loc:
        return jsonify({'error': 'mood and location are required'}), 400

    saved_ids = {(s.item_type, s.item_id) for s in current_user.saved}

    movies    = fetch_movies(mood)
    tvshows   = fetch_tvshows(mood)
    playlists = fetch_spotify_playlists(mood)
    acts_raw  = ACTIVITIES.get((mood, loc), [])

    for m in movies:
        m['saved'] = ('movie', m['id']) in saved_ids
    for t in tvshows:
        t['saved'] = ('tvshow', t['id']) in saved_ids
    for p in playlists:
        p['saved'] = ('playlist', p['id']) in saved_ids
    activities = [{**a, 'saved': ('activity', a['id']) in saved_ids}
                  for a in acts_raw]

    return jsonify({
        'mood':        mood,
        'location':    loc,
        'movies':      movies,
        'tvshows':     tvshows,
        'playlists':   playlists,
        'activities':  activities,
        'has_spotify': bool(SPOTIFY_CLIENT_ID),
        'has_tmdb':    bool(TMDB_API_KEY),
        'tmdb_key_set': bool(TMDB_API_KEY),
        'movie_count':  len(movies),
        'tv_count':     len(tvshows),
    })

@app.route('/api/v1/test-tmdb')
@api_login_required
def api_test_tmdb():
    """Debug endpoint — call this to verify your TMDB key works."""
    if not TMDB_API_KEY:
        return jsonify({
            'ok': False,
            'error': 'TMDB_API_KEY is not set in your .env file',
            'fix': 'Add TMDB_API_KEY=your_key to .env then restart python app.py'
        }), 400
    try:
        r = requests.get(f'{TMDB_BASE}/movie/popular', params={
            'api_key':  TMDB_API_KEY,
            'language': 'en-US',
            'page':     1,
        }, timeout=10)
        if r.status_code == 200:
            results = r.json().get('results', [])
            return jsonify({
                'ok':           True,
                'status_code':  200,
                'result_count': len(results),
                'sample_title': results[0]['title'] if results else None,
                'message':      'TMDB API key is working correctly!'
            })
        else:
            return jsonify({
                'ok':          False,
                'status_code': r.status_code,
                'error':       r.json().get('status_message', 'Unknown error'),
                'fix':         'Double-check your TMDB_API_KEY value in .env'
            }), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/v1/save', methods=['POST'])
@api_login_required
def api_save():
    d         = request.get_json(silent=True) or {}
    item_type = (d.get('item_type') or '').strip()
    item_id   = str(d.get('item_id') or '').strip()
    if not item_type or not item_id:
        return jsonify({'error': 'item_type and item_id required'}), 400

    VALID_TYPES = {'movie', 'tvshow', 'playlist', 'activity'}
    if item_type not in VALID_TYPES:
        return jsonify({'error': f'item_type must be one of {VALID_TYPES}'}), 400

    existing = SavedItem.query.filter_by(
        user_id=current_user.id,
        item_type=item_type,
        item_id=item_id).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'saved': False, 'message': 'Removed from saved'})

    new_item = SavedItem(
        user_id      = current_user.id,
        item_type    = item_type,
        item_id      = item_id,
        title        = (d.get('title') or '')[:200],
        subtitle     = (d.get('subtitle') or '')[:300],
        image_url    = (d.get('image') or '')[:500],
        external_url = (d.get('url') or '')[:500],
    )
    db.session.add(new_item)
    db.session.commit()
    return jsonify({'saved': True, 'message': 'Saved successfully', 'id': new_item.id})

@app.route('/api/v1/saved')
@api_login_required
def api_saved():
    items = SavedItem.query.filter_by(user_id=current_user.id)\
                .order_by(SavedItem.id.desc()).all()
    return jsonify({'saved': [{
        'id':           s.id,
        'item_type':    s.item_type,
        'item_id':      s.item_id,
        'title':        s.title,
        'subtitle':     s.subtitle,
        'image_url':    s.image_url,
        'external_url': s.external_url,
    } for s in items]})

@app.route('/api/v1/search/movies')
@api_login_required
def api_search_movies():
    q = request.args.get('q', '').strip()
    if not q: return jsonify({'error': 'q required'}), 400
    if not TMDB_API_KEY: return jsonify({'error': 'TMDB not configured'}), 503
    try:
        r = requests.get(f'{TMDB_BASE}/search/movie',
            params={'api_key': TMDB_API_KEY, 'query': q, 'language': 'en-US', 'page': 1},
            timeout=8)
        return jsonify({'results': [_movie(m) for m in r.json().get('results', [])[:8]]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/v1/search/tv')
@api_login_required
def api_search_tv():
    q = request.args.get('q', '').strip()
    if not q: return jsonify({'error': 'q required'}), 400
    if not TMDB_API_KEY: return jsonify({'error': 'TMDB not configured'}), 503
    try:
        r = requests.get(f'{TMDB_BASE}/search/tv',
            params={'api_key': TMDB_API_KEY, 'query': q, 'language': 'en-US', 'page': 1},
            timeout=8)
        return jsonify({'results': [_tvshow(s) for s in r.json().get('results', [])[:8]]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── PAGE ROUTES ───────────────────────────────────────────────────────────────

@app.route('/')
def home(): return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('mood'))
    error = None
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '').strip()
        c = request.form.get('confirm',  '').strip()
        if not u or not p:
            error = 'All fields are required.'
        elif len(p) < 6:
            error = 'Password must be at least 6 characters.'
        elif p != c:
            error = 'Passwords do not match.'
        elif User.query.filter_by(username=u).first():
            error = 'Username already taken.'
        else:
            user = User(username=u, password=generate_password_hash(p))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for('mood'))
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('mood'))
    error = None
    if request.method == 'POST':
        u = User.query.filter_by(
            username=request.form.get('username', '').strip()).first()
        if u and check_password_hash(u.password, request.form.get('password', '')):
            login_user(u)
            return redirect(url_for('mood'))
        error = 'Invalid username or password.'
    return render_template('login.html', error=error)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/mood')
@login_required
def mood(): return render_template('mood.html')

@app.route('/saved')
@login_required
def saved():
    items = SavedItem.query.filter_by(user_id=current_user.id)\
                .order_by(SavedItem.id.desc()).all()
    return render_template('saved.html', saved=items)

# ── INIT ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
