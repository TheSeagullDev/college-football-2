import cfbd
import os
from cfbd.rest import ApiException
from dotenv import load_dotenv
import os
from app import app, db, Game

load_dotenv()

# Defining the host is optional and defaults to https://api.collegefootballdata.com
# See configuration.py for a list of all supported configuration parameters.
configuration = cfbd.Configuration(
    host = "https://api.collegefootballdata.com"
)

# The client must configure the authentication and authorization parameters
# in accordance with the API server security policy.
# Examples for each auth method are provided below, use the example that
# satisfies your auth use case.

# Configure Bearer authorization: apiKey
configuration = cfbd.Configuration(
    access_token = os.environ.get("CFBD_API_KEY")
)


# Enter a context with an instance of the API client
with cfbd.ApiClient(configuration) as api_client:
    lines_instance = cfbd.BettingApi(api_client)
    games_instance = cfbd.GamesApi(api_client)

    year = 2025
    season_type = cfbd.SeasonType("postseason")
    classification = cfbd.DivisionClassification("fbs")

    try:
        lines_list = lines_instance.get_lines(year=year, season_type=season_type)
        games_list = games_instance.get_games(year=year, season_type=season_type, classification=classification)

        # ---- ADD THIS PART HERE ----
        # Build lookup dict: game_id -> game metadata
        games_by_id = {g.id: g for g in games_list}
        # ----------------------------

        with app.app_context():
            for g in lines_list:

                # metadata for this game, if available
                meta = games_by_id.get(g.id)
                title = meta.notes if meta else None

                existing = Game.query.filter_by(
                    id=g.id
                ).first()

                spread = g.lines[0].spread if len(g.lines) > 0 else 0

                if existing:
                    existing.line = spread
                    existing.home_score = g.home_score
                    existing.away_score = g.away_score
                    existing.completed = meta.completed
                elif meta:
                    game = Game(
                        id=g.id,
                        home_team=g.home_team,
                        away_team=g.away_team,
                        home_id=meta.home_id,
                        away_id=meta.away_id,
                        home_score=g.home_score,
                        away_score=g.away_score,
                        title=title,
                        line=spread,
                        completed=meta.completed,
                        start_date=meta.start_date,
                        point_value=2,
                        is_playoff="Playoff" in title
                    )
                    db.session.add(game)

            db.session.commit()

        print(f"Updated DB with {len(games_list)} games for {year}!")

    except ApiException as e:
        print("Exception when calling the API: %s\n" % e)