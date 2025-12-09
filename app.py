from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
import os, hashlib
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from datetime import datetime, timedelta
from dotenv import load_dotenv
import resend


load_dotenv()

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite3')
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.secret_key = os.environ.get("SECRET_KEY")

resend.api_key = os.environ.get("RESEND_API_KEY")

db = SQLAlchemy(app)

class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    home_team = db.Column(db.String(50))
    away_team = db.Column(db.String(50))
    home_id = db.Column(db.Integer)
    away_id = db.Column(db.Integer)
    home_score = db.Column(db.Integer)
    away_score = db.Column(db.Integer)
    title = db.Column(db.String(100))
    line = db.Column(db.Float)
    point_value = db.Column(db.Integer)
    start_date = db.Column(db.DateTime(timezone=True))
    completed = db.Column(db.Boolean, default=False)
    is_playoff = db.Column(db.Boolean, default=False)

class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    seed = db.Column(db.Integer, nullable=True)
    espn_id = db.Column(db.Integer)

class PlayoffGame(db.Model):
    __tablename__ = "playoff_games"

    id = db.Column(db.Integer, primary_key=True)
    round = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String, nullable=False)  # e.g. "Quarterfinal 1"
    
    # these reference earlier games; null if it's round 1
    depends_on_game1 = db.Column(db.Integer, db.ForeignKey("playoff_games.id"), nullable=True)
    depends_on_game2 = db.Column(db.Integer, db.ForeignKey("playoff_games.id"), nullable=True)

    team1_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)
    team2_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)

    # if there's a bye, that team fills slot 1 automatically
    bye_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)

    # ESPN ID set manually once matchup exists IRL
    espn_id = db.Column(db.String, nullable=True)

    final_score_team1 = db.Column(db.Integer, nullable=True)
    final_score_team2 = db.Column(db.Integer, nullable=True)
    winner_team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=True)

    team1 = db.relationship("Team", foreign_keys=[team1_id])
    team2 = db.relationship("Team", foreign_keys=[team2_id])
    bye_team = db.relationship("Team", foreign_keys=[bye_team_id])
    winner = db.relationship("Team", foreign_keys=[winner_team_id])

class PlayoffPick(db.Model):
    __tablename__ = "playoff_picks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    playoff_game_id = db.Column(db.Integer, db.ForeignKey("playoff_games.id"), nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    team = db.relationship("Team", foreign_keys=[team_id])

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(50), nullable=False)
    score = db.Column(db.Integer)
    password_hash = db.Column(db.String(255), nullable=False)

    def __init__(self, **kwargs):
        raw_password = kwargs.pop("password", None)
        if raw_password is not None:
            kwargs["password_hash"] = generate_password_hash(raw_password)

        super().__init__(**kwargs)

    # backref gives you "user.picks"
    picks = db.relationship("Pick", backref="user", lazy=True)

class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # foreign key to User
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # foreign key to the Game table
    game_id = db.Column(db.Integer, db.ForeignKey('game.id'), nullable=False)

    # whatever else your pick needs
    chosen_team = db.Column(db.String(50))

    # game relationship (so "pick.game" works)
    game = db.relationship("Game", backref="picks", lazy=True)

class MagicLinkToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(255), nullable=False)
    token_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

def update_scores():
    with app.app_context():
        # RESET
        db.session.query(User).update({User.score: 0})
        db.session.commit()

        # ---------- REGULAR SEASON ----------
        picks = Pick.query.all()
        for pick in picks:
            g = pick.game
            if not g.completed:
                continue

            user = User.query.get(pick.user_id)

            home = g.home_score
            away = g.away_score
            diff = home - away + g.line

            if diff > 0:
                winner = g.home_team
            elif diff < 0:
                winner = g.away_team
            else:
                winner = "push"

            if pick.chosen_team == winner:
                user.score += g.point_value
                db.session.add(user)

        db.session.commit()

        # ---------- PLAYOFFS ----------
        playoff_picks = PlayoffPick.query.all()

        for pp in playoff_picks:
            pg = PlayoffGame.query.get(pp.playoff_game_id)
            user = User.query.get(pp.user_id)

            if not pg.espn_id:
                continue

            real_game = Game.query.filter_by(id=pg.espn_id).first()
            if not real_game or not real_game.completed:
                continue

            # STRAIGHT WINNER (NO SPREAD)
            home = real_game.home_score
            away = real_game.away_score

            if home > away:
                real_winner = real_game.home_team
            elif away > home:
                real_winner = real_game.away_team
            else:
                real_winner = "push"

            # multiplier: 2 × round
            round_points = 2 * pg.round
            if pp.team.name == real_winner:
                user.score += round_points
                db.session.add(user)

        db.session.commit()


def create_magic_link(email):
    token = os.urandom(32).hex()
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    record = MagicLinkToken(
        user_email=email,
        token_hash=token_hash,
        expires_at=datetime.utcnow() + timedelta(minutes=15)
    )

    db.session.add(record)
    db.session.commit()

    return f"https://football.noahsiegel.dev/verify?token={token}"

def send_magic_link(email):
    url = create_magic_link(email)

    magic_link_email_html = """\
    <!DOCTYPE html>
    <html>
    <body style="font-family: Arial, sans-serif; background-color:#f4f4f4; margin:0; padding:0;">
        <div style="max-width:600px; margin:40px auto; background:#fff; padding:20px; border-radius:8px;">
        <h2 style="color:#333;">Your Magic Login Link</h2>
        <p style="color:#555;">Hello,</p>
        <p style="color:#555;">Click the button below to log in to your account. This link will expire shortly and can only be used once:</p>
        <p><a href="{url}" style="display:inline-block; padding:12px 20px; background:#007BFF; color:#fff; text-decoration:none; border-radius:5px;">Log In</a></p>
        <p style="color:#555;">If you did not request this login link, you can safely ignore this email.</p>
        </div>
    </body>
    </html>
    """


    params: resend.Emails.SendParams = {
        "from": "College Football <login@football.noahsiegel.dev>",
        "to": email,
        "subject": "Login Link",
        "html": magic_link_email_html.format(url=url)
    }

    email = resend.Emails.send(params)

# returns bracket dict and visible picks dict in one deterministic pass
def build_bracket_and_visible_playoff(playoff_games, user_playoff):
    """
    Args:
      playoff_games: list of PlayoffGame ordered by round asc (PlayoffGame.query.order_by(...).all())
      user_playoff: dict mapping playoff_game_id -> team_id (DB picks)
    Returns:
      bracket: { pg_id: {"team1": id or None, "team2": id or None} }
      visible_playoff: { pg_id: team_id or None }  # what template should render as picked
    """

    # map games by id for quick lookup
    games_by_id = {pg.id: pg for pg in playoff_games}

    # initialize bracket with DB-provided teams (round 1) or Nones
    bracket = {}
    visible_playoff = {}

    # ensure playoff_games are processed in round order (and predictable within a round)
    # If your query didn't already order by round and id, do it here
    playoff_games_sorted = sorted(playoff_games, key=lambda g: (g.round, g.id))

    for pg in playoff_games_sorted:
        # default from DB columns
        t1 = pg.team1_id
        t2 = pg.team2_id

        # if the game depends on earlier games, derive the slot(s) from visible winners
        if pg.depends_on_game1:
            # winner of depends_on_game1 is whatever visible_pick was stored for that game
            t1 = visible_playoff.get(pg.depends_on_game1) or None
        if pg.depends_on_game2:
            t2 = visible_playoff.get(pg.depends_on_game2) or None

        # if there's a bye and no explicit team1, use bye
        if (not t1) and pg.bye_team_id:
            t1 = pg.bye_team_id

        bracket[pg.id] = {"team1": t1, "team2": t2}

        # decide what pick should be visible (i.e., is it still valid?)
        db_pick = user_playoff.get(pg.id)  # maybe None

        if db_pick and db_pick in (t1, t2):
            # valid — show the user's pick
            visible_playoff[pg.id] = db_pick
        else:
            # invalid or missing — nothing should be checked
            visible_playoff[pg.id] = None

        # IMPORTANT: visible_playoff[pg.id] represents that game's winner for downstream games
        # For downstream rounds, the "winner" slot is the user's pick if present, otherwise None.
        # If you want to treat an unpicked but single-team game as auto-advance, change logic above.
        # (currently we only advance what's visible)
        # If you want auto-advance for byes: visible_playoff will already be pg.bye_team_id above
    return bracket, visible_playoff




def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            flash("Logged in successfully!")
            return redirect("/")  # or wherever

        return "Invalid credentials", 401

    return render_template("login.html")

@app.route("/request", methods=["GET", "POST"])
def request_login():
    if request.method == "POST":
        email = request.form.get("email")

        # Always respond the same — don’t leak who exists
        send_magic_link(email)
        return "If that email exists, a link was sent."
    else:
        return render_template("passwordless.html")


@app.route("/verify")
def verify_login():
    raw = request.args.get("token")
    token_hash = hashlib.sha256(raw.encode()).hexdigest()

    record = MagicLinkToken.query.filter_by(token_hash=token_hash).first()

    if not record or record.expires_at < datetime.utcnow():
        return "Invalid or expired", 400

    # Log in user
    user = User.query.filter_by(email=record.user_email).first()
    session["user_id"] = user.id

    # One-time use — delete token
    db.session.delete(record)
    db.session.commit()

    return redirect("/")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logged out.")
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" not in session:
        if request.method == "POST":
            email = request.form.get("email")
            name = request.form.get("name")
            password = request.form.get("password")

            # basic validation
            if not email or not password:
                flash("Please enter a email and a passowrd")
                return redirect("/register")

            # check if user exists
            existing = User.query.filter_by(email=email).first()
            if existing:
                flash("Email already taken.")
                return redirect("/register")

            # create user
            user = User(email=email, password=password, name=name, score=0)
            db.session.add(user)
            db.session.commit()

            # log them in immediately
            session["user_id"] = user.id

            flash("Welcome!")
            return redirect("/")
    else:
        return redirect("/")
    return render_template("register.html")

@app.route("/")
def index():
    user = User.query.filter_by(id=session.get("user_id")).first()
    if user:
        picks = len(Pick.query.filter_by(user_id=user.id).all())
        playoff_picks = len(PlayoffPick.query.filter_by(user_id=user.id).all())
        PLAYOFF_GAMES = 11
        pick_count = picks + playoff_picks
        total_available = len(Game.query.filter_by(is_playoff=False).all()) + PLAYOFF_GAMES
        return render_template("index.html", user=user, pick_count=pick_count, total_available=total_available)
    else:
        return render_template("index.html", user=user)

@app.route("/picks")
@login_required
def picks():
    user_id = session["user_id"]

    games = Game.query.filter_by(is_playoff=False).all()
    picks = Pick.query.filter_by(user_id=user_id).all()
    user_picks = {p.game_id: p.chosen_team for p in picks}

    playoff_games = PlayoffGame.query.order_by(PlayoffGame.round, PlayoffGame.id).all()
    playoff_picks = PlayoffPick.query.filter_by(user_id=user_id).all()
    user_playoff = {p.playoff_game_id: p.team_id for p in playoff_picks}

    bracket, visible_playoff = build_bracket_and_visible_playoff(playoff_games, user_playoff)

    picks_number = len(Pick.query.filter_by(user_id=user_id).all())
    playoff_pick_number = len(PlayoffPick.query.filter_by(user_id=user_id).all())
    PLAYOFF_GAMES = 11
    pick_count = picks_number + playoff_pick_number
    total_available = len(Game.query.filter_by(is_playoff=False).all()) + PLAYOFF_GAMES

    teams = {t.id: t for t in Team.query.all()}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template(
            "picks_inner.html",
            games=games,
            user_picks=user_picks,
            playoff_games=playoff_games,
            user_playoff=visible_playoff,   # make template use this name
            bracket=bracket,
            teams=teams,
            pick_count=pick_count,
            total_available=total_available
        )
    return render_template(
            "picks.html",
            games=games,
            user_picks=user_picks,
            playoff_games=playoff_games,
            user_playoff=visible_playoff,   # make template use this name
            bracket=bracket,
            teams=teams,
            pick_count=pick_count,
            total_available=total_available
        )



@app.route("/api/save_pick", methods=["POST"])
@login_required
def save_pick():
    data = request.get_json()
    game_id = data.get("game_id")
    pick_value = data.get("pick")
    user_id = session.get("user_id")
    user = User.query.filter_by(id=user_id).first()

    # check game exists
    game = Game.query.get_or_404(game_id)

    # see if user already has a pick for that game
    existing = Pick.query.filter_by(user_id=user.id, game_id=game_id).first()

    if existing:
        existing.chosen_team = pick_value
    else:
        new_pick = Pick(
            user_id=user.id,
            game_id=game_id,
            chosen_team=pick_value
        )
        db.session.add(new_pick)

    db.session.commit()
    return {"status": "ok"}

@app.route("/api/save_playoff_pick", methods=["POST"])
@login_required
def save_playoff_pick():
    data = request.get_json()
    user_id = session["user_id"]
    playoff_game_id = data["playoff_game_id"]
    team_id = data["team_id"]

    pick = PlayoffPick.query.filter_by(user_id=user_id, playoff_game_id=playoff_game_id).first()

    if not pick:
        pick = PlayoffPick(user_id=user_id, playoff_game_id=playoff_game_id)

    pick.team_id = team_id
    db.session.add(pick)
    db.session.commit()

    return {"success": True}


@app.route("/standings")
@login_required
def standings():
    update_scores()
    users = User.query.order_by(User.score.desc()).all()
    
    leaderboard = []
    current_rank = 0
    prev_score = None
    index = 0  # how many users we've processed

    for user in users:
        index += 1

        # If score changes, this user gets a new rank equal to their index.
        # If score is the same as previous, they share the same rank.
        if user.score != prev_score:
            current_rank = index
            prev_score = user.score

        leaderboard.append({
            "id": user.id,
            "name": user.name,
            "score": user.score,
            "rank": current_rank
        })

    return render_template("standings.html", leaderboard=leaderboard)

@app.route("/help", methods=["GET", "POST"])
def help():
    if request.method == "POST":
        email = request.form.get("email")
        message = request.form.get("message")
        params: resend.Emails.SendParams = {
            "from": "College Football Help Request <help@football.noahsiegel.dev>",
            "to": "njsiegel9@gmail.com",
            "subject": "Help Request",
            "html": f"Email: {email} Message: {message}"
        }

        email = resend.Emails.send(params)
        flash("Message sent!")
        return redirect("/")
    else:
        return render_template("help.html")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)