import cfbd
import os
from cfbd.rest import ApiException
from dotenv import load_dotenv
import os
from app import app
from models import db, Game

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
    # Create an instance of the API class
    api_instance = cfbd.BettingApi(api_client)
    year = 2025 # int | Optional year filter (optional)
    week = 13

    try:
        games_list = api_instance.get_lines(year=year, week=week)
        with app.app_context():
            for g in games_list:
                existing = Game.query.filter_by(
                    home_team=g.home_team,
                    away_team=g.away_team
                ).first()
                if existing:
                    existing.line = line=g.lines[0].spread
                else:
                    if len(g.lines) > 0:
                        game = Game(
                            home_team=g.home_team,
                            away_team=g.away_team,
                            line=g.lines[0].spread
                        )
                    else:
                        game = Game(
                                home_team=g.home_team,
                                away_team=g.away_team,
                                line=0
                            )
                    db.session.add(game)
            db.session.commit()
        print(f"Updated DB with {len(games_list)} games for {year}!")
    except ApiException as e:
        print("Exception when calling AdjustedMetricsApi->get_adjusted_player_passing_stats: %s\n" % e)
