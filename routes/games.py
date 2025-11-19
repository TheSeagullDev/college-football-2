from flask import Blueprint, render_template
from models import db, Game

games_bp = Blueprint("games", __name__)

@games_bp.route("/")
def index():
    games = Game.query.all()
    return render_template("index.html", games=games)