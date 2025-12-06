from flask import Flask, render_template, request, session, redirect, url_for, flash
import os
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'db.sqlite3')
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.secret_key = "CHANGE_THIS"

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

def update_scores():
    with app.app_context():
        picks = Pick.query.filter_by().all()

        db.session.query(User).update({User.score: 0})
        db.session.commit()


        for pick in picks:
            if pick.game.completed:
                user = User.query.filter_by(id=pick.user_id).first()
                home = pick.game.home_score
                away = pick.game.away_score
                diff = home - away + pick.game.line
                if diff > 0:
                    winner = pick.game.home_team
                elif diff < 0:
                    winner = pick.game.away_team
                else:
                    winner = "push"
                if pick.chosen_team == winner:
                    user.score += pick.game.point_value
                    db.session.commit()


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
            return redirect("/")  # or wherever

        return "Invalid credentials", 401

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user_id", None)
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
                flash("Bro… you need a email AND a password.")
                return redirect("/register")

            # check if user exists
            existing = User.query.filter_by(email=email).first()
            if existing:
                flash("email already taken.")
                return redirect("/register")

            # create user
            user = User(email=email, password=password, name=name, score=0)
            db.session.add(user)
            db.session.commit()

            # log them in immediately
            session["user_id"] = user.id

            flash("Welcome aboard.")
            return redirect("/")
    else:
        return redirect("/")
    return render_template("register.html")

@app.route("/")
def index():
    user = User.query.filter_by(id=session.get("user_id")).first()
    return render_template("index.html", user=user)

@app.route("/picks")
@login_required
def picks():
    user_id = session.get("user_id")
    user = User.query.filter_by(id=user_id).first()

    games = Game.query.order_by(Game.is_playoff.asc(), Game.start_date.asc()).all()
    picks = Pick.query.filter_by(user_id=user_id).all()
    user_picks = {p.game_id: p.chosen_team for p in picks}
    return render_template("picks.html", games=games, user_picks=user_picks)

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

@app.route("/standings")
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


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)