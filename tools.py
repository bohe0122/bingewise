"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import os
from datetime import date, datetime, timedelta

import requests


TMDB_URL = "https://api.themoviedb.org/3"


# ============================================================
# TMDB helper
# ============================================================

def _tmdb_get(path: str, params: dict | None = None) -> dict:
    """Send a request to TMDB and return the JSON response safely."""

    key = os.environ.get("TMDB_API_KEY", "").strip()

    if not key:
        raise RuntimeError(
            "TMDB_API_KEY is not set. TV show data is currently unavailable."
        )

    params = dict(params or {})
    params["api_key"] = key

    try:
        response = requests.get(
            f"{TMDB_URL}{path}",
            params=params,
            timeout=10,
        )

    except requests.RequestException:
        raise RuntimeError(
            "Could not reach TMDB. Try the same request again."
        )

    if response.status_code == 401:
        raise RuntimeError(
            "TMDB authentication failed. Check the server API key configuration."
        )

    if response.status_code == 404:
        raise RuntimeError(
            "TMDB could not find that show. "
            "Use search_show to get a valid show_id."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "TMDB rate limit was reached. Wait briefly and try again."
        )

    if not response.ok:
        raise RuntimeError(
            f"TMDB returned HTTP {response.status_code}. Try again."
        )

    return response.json()


# ============================================================
# Load show episode information
# ============================================================

def _load_show(show_id: int) -> dict:
    """Load a TV show's aired episodes and runtimes from TMDB."""

    details = _tmdb_get(f"/tv/{show_id}")

    episodes = []
    estimated_episodes = 0
    unaired_episodes = 0

    today = date.today()

    for season in details.get("seasons", []):
        season_number = season.get("season_number")

        # Season 0 usually contains specials, so skip it.
        if not season_number:
            continue

        season_data = _tmdb_get(
            f"/tv/{show_id}/season/{season_number}"
        )

        season_episodes = season_data.get("episodes", [])

        aired_episodes = []

        for episode in season_episodes:
            air_date = episode.get("air_date")

            # Exclude episodes with a known future air date.
            if air_date:
                try:
                    episode_air_date = datetime.strptime(
                        air_date,
                        "%Y-%m-%d",
                    ).date()

                    if episode_air_date > today:
                        unaired_episodes += 1
                        continue

                except ValueError:
                    pass

            aired_episodes.append(episode)

        # Calculate an average runtime for this season.
        # This is used only if TMDB is missing a runtime.
        known_runtimes = [
            episode.get("runtime")
            for episode in aired_episodes
            if episode.get("runtime")
        ]

        if known_runtimes:
            average_runtime = round(
                sum(known_runtimes) / len(known_runtimes)
            )
        else:
            average_runtime = 45

        for episode in aired_episodes:
            runtime = episode.get("runtime")

            if not runtime:
                runtime = average_runtime
                estimated_episodes += 1

            episodes.append({
                "season": season_number,
                "episode": episode["episode_number"],
                "title": episode.get("name", ""),
                "minutes": runtime,
            })

    return {
        "show_id": show_id,
        "name": details.get("name"),
        "episodes": episodes,
        "estimated_episodes": estimated_episodes,
        "unaired_episodes": unaired_episodes,
    }


# ============================================================
# Tool 1: Search for a TV show
# ============================================================

def search_show(query: str) -> str:
    """Search TMDB for a TV series by title and return possible matches."""

    if not query or not query.strip():
        return json.dumps({
            "error": (
                "The show title is empty. "
                "Provide a TV show title to search for."
            )
        })

    try:
        data = _tmdb_get(
            "/search/tv",
            {
                "query": query.strip(),
                "include_adult": "false",
            },
        )

    except RuntimeError as e:
        return json.dumps({
            "error": str(e)
        })

    results = data.get("results", [])[:5]

    if not results:
        return json.dumps({
            "error": (
                f"No TV show matching '{query}' was found. "
                "Check the title or spelling."
            )
        })

    return json.dumps({
        "matches": [
            {
                "show_id": show["id"],
                "name": show.get("name"),
                "original_name": show.get("original_name"),
                "first_air_date": show.get("first_air_date"),
                "overview": show.get("overview"),
            }
            for show in results
        ]
    })


# ============================================================
# Tool 2: Get total watch time
# ============================================================

def get_watch_time(show_id: int) -> str:
    """Calculate the total watch time of all aired episodes of a TV show."""

    try:
        show = _load_show(int(show_id))

    except RuntimeError as e:
        return json.dumps({
            "error": str(e)
        })

    except (TypeError, ValueError):
        return json.dumps({
            "error": (
                f"show_id must be a number returned by search_show, "
                f"got '{show_id}'."
            )
        })

    if not show["episodes"]:
        return json.dumps({
            "error": (
                f"No aired episode runtime information "
                f"was found for {show['name']}."
            )
        })

    total_minutes = sum(
        episode["minutes"]
        for episode in show["episodes"]
    )

    unaired_episodes = show.get(
        "unaired_episodes",
        0,
    )

    if show["estimated_episodes"] > 0:
        runtime_note = (
            f"{show['estimated_episodes']} aired episode runtime(s) "
            "were estimated using the season average."
        )
    else:
        runtime_note = (
            "All aired episode runtimes came directly from TMDB."
        )

    if unaired_episodes > 0:
        airing_note = (
            f"{unaired_episodes} future episode(s) listed by TMDB "
            "were excluded because they have not aired yet."
        )
    else:
        airing_note = (
            "No future episodes with known air dates were included."
        )

    return json.dumps({
        "show_id": show["show_id"],
        "name": show["name"],
        "total_episodes": len(show["episodes"]),
        "total_minutes": total_minutes,
        "total_hours": round(total_minutes / 60, 1),
        "estimated_episodes": show["estimated_episodes"],
        "unaired_episodes": unaired_episodes,
        "note": f"{runtime_note} {airing_note}",
    })


# ============================================================
# Shared schedule simulation
# ============================================================

def _simulate_schedule(
    episodes,
    start,
    weekday_minutes,
    weekend_minutes,
    end_date=None,
    max_days=400,
):
    """
    Simulate a viewing schedule using whole episodes.

    If end_date is provided, stop after that date.
    Otherwise, continue until all episodes are finished.
    """

    schedule = []
    episode_index = 0
    current_date = start
    days_checked = 0

    while (
        episode_index < len(episodes)
        and days_checked < max_days
    ):

        if (
            end_date is not None
            and current_date > end_date
        ):
            break

        is_weekend = current_date.weekday() >= 5

        if is_weekend:
            available_minutes = weekend_minutes
        else:
            available_minutes = weekday_minutes

        watched_today = []
        used_minutes = 0

        while episode_index < len(episodes):
            episode = episodes[episode_index]

            if (
                used_minutes + episode["minutes"]
                > available_minutes
            ):
                break

            watched_today.append({
                "season": episode["season"],
                "episode": episode["episode"],
                "title": episode["title"],
                "minutes": episode["minutes"],
            })

            used_minutes += episode["minutes"]
            episode_index += 1

        if watched_today:
            schedule.append({
                "date": current_date.isoformat(),
                "day_type": (
                    "weekend"
                    if is_weekend
                    else "weekday"
                ),
                "available_minutes": available_minutes,
                "planned_minutes": used_minutes,
                "episodes": watched_today,
            })

        current_date += timedelta(days=1)
        days_checked += 1

    return schedule, episode_index


# ============================================================
# Tool 3: Original BingeWise scheduling algorithm
# ============================================================

def plan_binge_schedule(
    show_id: int,
    weekday_minutes: int,
    weekend_minutes: int,
    deadline: str,
    season: int | None = None,
) -> str:
    """
    Create a personalized day-by-day TV watching schedule.

    The schedule starts today, uses real episode runtimes from TMDB,
    keeps episodes whole, and checks whether the user can finish
    before the requested deadline.

    If season is provided, only episodes from that season are used.

    If the user cannot finish by the deadline, the tool calculates
    the projected finish date and recommends a higher daily
    viewing time.
    """

    # --------------------------------------------------------
    # Load show
    # --------------------------------------------------------

    try:
        show = _load_show(int(show_id))

    except RuntimeError as e:
        return json.dumps({
            "error": str(e)
        })

    except (TypeError, ValueError):
        return json.dumps({
            "error": (
                f"show_id must be a number returned by search_show, "
                f"got '{show_id}'."
            )
        })

    start = date.today()

    # --------------------------------------------------------
    # Validate deadline
    # --------------------------------------------------------

    try:
        end = datetime.strptime(
            deadline,
            "%Y-%m-%d",
        ).date()

    except (TypeError, ValueError):
        return json.dumps({
            "error": (
                "The deadline must use YYYY-MM-DD format. "
                "For example: 2026-10-31."
            )
        })

    if end < start:
        return json.dumps({
            "error": (
                f"The deadline ({end.isoformat()}) has already passed. "
                f"Today is {start.isoformat()}. "
                "Please choose a future deadline."
            )
        })

    # --------------------------------------------------------
    # Validate viewing time
    # --------------------------------------------------------

    try:
        weekday_minutes = int(weekday_minutes)
        weekend_minutes = int(weekend_minutes)

    except (TypeError, ValueError):
        return json.dumps({
            "error": (
                "weekday_minutes and weekend_minutes "
                "must be numbers."
            )
        })

    if weekday_minutes < 0 or weekend_minutes < 0:
        return json.dumps({
            "error": (
                "Available watch time cannot be negative."
            )
        })

    if (
        weekday_minutes == 0
        and weekend_minutes == 0
    ):
        return json.dumps({
            "error": (
                "You need at least some available watch time "
                "during the week or weekend."
            )
        })

    episodes = show["episodes"]

    # --------------------------------------------------------
    # Optional season filter
    # --------------------------------------------------------

    selected_season = None

    if season is not None:
        try:
            selected_season = int(season)
        except (TypeError, ValueError):
            return json.dumps({
                "error": (
                    f"season must be a whole number, got '{season}'."
                )
            })

        if selected_season < 1:
            return json.dumps({
                "error": (
                    "season must be 1 or greater."
                )
            })

        episodes = [
            episode
            for episode in episodes
            if episode["season"] == selected_season
        ]

        if not episodes:
            available_seasons = sorted({
                episode["season"]
                for episode in show["episodes"]
            })

            return json.dumps({
                "error": (
                    f"No aired episodes were found for season "
                    f"{selected_season} of {show['name']}. "
                    f"Available aired seasons: {available_seasons}."
                )
            })

    if not episodes:
        return json.dumps({
            "error": (
                f"No episode information was found for "
                f"{show['name']}."
            )
        })

    # --------------------------------------------------------
    # Check longest episode
    # --------------------------------------------------------

    longest_episode = max(
        episode["minutes"]
        for episode in episodes
    )

    longest_available_time = max(
        weekday_minutes,
        weekend_minutes,
    )

    if longest_available_time < longest_episode:
        return json.dumps({
            "error": (
                f"{show['name']} has an episode that is "
                f"{longest_episode} minutes long, but your largest "
                f"daily watch window is only "
                f"{longest_available_time} minutes. "
                "Because BingeWise keeps episodes whole, "
                "increase your available time to at least "
                f"{longest_episode} minutes on at least one day type."
            )
        })

    # --------------------------------------------------------
    # Generate full schedule
    # --------------------------------------------------------

    schedule, episode_index = _simulate_schedule(
        episodes,
        start,
        weekday_minutes,
        weekend_minutes,
    )

    if episode_index < len(episodes):
        return json.dumps({
            "error": (
                "The schedule could not be completed within "
                "400 days. Try increasing your weekday or "
                "weekend viewing time."
            )
        })

    finish_date_string = schedule[-1]["date"]

    finish_date = datetime.strptime(
        finish_date_string,
        "%Y-%m-%d",
    ).date()

    # --------------------------------------------------------
    # Deadline result
    # --------------------------------------------------------

    makes_deadline = finish_date <= end

    if makes_deadline:
        days_to_spare = (
            end - finish_date
        ).days

        days_late = 0

    else:
        days_to_spare = 0

        days_late = (
            finish_date - end
        ).days

    # --------------------------------------------------------
    # Count progress at deadline from existing schedule
    # --------------------------------------------------------

    episodes_completed_by_deadline = sum(
        len(day["episodes"])
        for day in schedule
        if day["date"] <= end.isoformat()
    )

    episodes_remaining_at_deadline = (
        len(episodes)
        - episodes_completed_by_deadline
    )

    minutes_remaining_at_deadline = sum(
        episode["minutes"]
        for episode in episodes[
            episodes_completed_by_deadline:
        ]
    )

    # --------------------------------------------------------
    # Find recommended viewing time if user misses deadline
    # --------------------------------------------------------

    recommended_weekday_minutes = None
    recommended_weekend_minutes = None

    if not makes_deadline:

        for percent in range(
            110,
            310,
            10,
        ):

            test_weekday_minutes = round(
                weekday_minutes
                * percent
                / 100
            )

            test_weekend_minutes = round(
                weekend_minutes
                * percent
                / 100
            )

            _, test_episode_index = _simulate_schedule(
                episodes,
                start,
                test_weekday_minutes,
                test_weekend_minutes,
                end_date=end,
            )

            if test_episode_index == len(episodes):

                recommended_weekday_minutes = (
                    test_weekday_minutes
                )

                recommended_weekend_minutes = (
                    test_weekend_minutes
                )

                break

    # --------------------------------------------------------
    # Runtime summary
    # --------------------------------------------------------

    total_minutes = sum(
        episode["minutes"]
        for episode in episodes
    )

    total_hours = round(
        total_minutes / 60,
        1,
    )

    # --------------------------------------------------------
    # Final result
    # --------------------------------------------------------

    return json.dumps({
        "show_id": show["show_id"],
        "name": show["name"],
        "season": selected_season,

        "total_episodes": len(episodes),
        "total_minutes": total_minutes,
        "total_hours": total_hours,

        "start_date": start.isoformat(),
        "deadline": end.isoformat(),

        "weekday_minutes": weekday_minutes,
        "weekend_minutes": weekend_minutes,

        "makes_deadline": makes_deadline,
        "projected_finish_date": (
            finish_date.isoformat()
        ),

        "days_to_spare": days_to_spare,
        "days_late": days_late,

        "episodes_completed_by_deadline":
            episodes_completed_by_deadline,

        "episodes_remaining_at_deadline":
            episodes_remaining_at_deadline,

        "minutes_remaining_at_deadline":
            minutes_remaining_at_deadline,

        "recommended_weekday_minutes":
            recommended_weekday_minutes,

        "recommended_weekend_minutes":
            recommended_weekend_minutes,

        "schedule": schedule,
    })


# ============================================================
# Tool definitions Gemini sees
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_show",
            "description": (
                "Search TMDB for a TV series by title and get its show_id. "
                "Call this first whenever the user names a TV show. "
                "The other TV tools require the show_id returned by this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The TV show title to search for, "
                            "for example 'Breaking Bad'."
                        ),
                    },
                },
                "required": [
                    "query",
                ],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "get_watch_time",
            "description": (
                "Get the real aired episode count and total runtime "
                "of a TV series using TMDB data. Use this when the user "
                "asks how many episodes a show has or how long it takes "
                "to watch. Never estimate these values yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "show_id": {
                        "type": "integer",
                        "description": (
                            "The show_id returned by search_show."
                        ),
                    },
                },
                "required": [
                    "show_id",
                ],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "plan_binge_schedule",
            "description": (
                "Build a personalized day-by-day TV watching plan "
                "starting today using real episode runtimes. Keep "
                "episodes whole, check whether the user can finish "
                "before their deadline, calculate the projected finish "
                "date, and recommend more viewing time if necessary. "
                "Use this whenever the user asks whether they can finish "
                "a show or a specific season by a certain date, or asks "
                "for a watch schedule. If the user specifies a season, "
                "pass that season number in the optional season argument. "
                "If no season is specified, plan for the entire aired series. "
                "Call it again when the user changes their availability "
                "or deadline."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "show_id": {
                        "type": "integer",
                        "description": (
                            "The show_id returned by search_show."
                        ),
                    },

                    "weekday_minutes": {
                        "type": "integer",
                        "description": (
                            "Minutes available to watch per weekday "
                            "(Monday through Friday). "
                            "For example, 2 hours = 120 minutes."
                        ),
                    },

                    "weekend_minutes": {
                        "type": "integer",
                        "description": (
                            "Minutes available to watch per weekend day "
                            "(Saturday and Sunday). "
                            "For example, 4 hours = 240 minutes."
                        ),
                    },

                    "deadline": {
                        "type": "string",
                        "description": (
                            "The last day the user wants to finish "
                            "the show, in YYYY-MM-DD format."
                        ),
                    },

                    "season": {
                        "type": "integer",
                        "description": (
                            "Optional season number to plan for. "
                            "For example, use 1 if the user asks to "
                            "finish season 1. Omit this argument when "
                            "the user wants to watch the entire series."
                        ),
                    },
                },
                "required": [
                    "show_id",
                    "weekday_minutes",
                    "weekend_minutes",
                    "deadline",
                ],
            },
        },
    },
]


# ============================================================
# Tool map
# ============================================================

TOOL_MAP = {
    "search_show": search_show,
    "get_watch_time": get_watch_time,
    "plan_binge_schedule": plan_binge_schedule,
}


# ============================================================
# Tool runner
# ============================================================

def run_tool(name: str, args: dict) -> str:
    """Run one tool call without letting a bad tool call crash the chat."""

    if name not in TOOL_MAP:
        return json.dumps({
            "error": (
                f"Unknown tool '{name}'. "
                f"Available tools: {list(TOOL_MAP)}"
            )
        })

    try:
        return TOOL_MAP[name](**args)

    except TypeError as e:
        return json.dumps({
            "error": (
                f"Bad arguments for {name}: {e}"
            )
        })

    except Exception as e:
        return json.dumps({
            "error": (
                f"{name} failed: {type(e).__name__}"
            )
        })