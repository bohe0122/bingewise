# gemini-web-tool-calling
# BingeWise

BingeWise is a web-based TV watch planner that helps users figure out whether they can finish a TV show before a deadline/day based on their available viewing time.

The agent uses real TV show and episode runtime data from TMDB and creates a personalized watching schedule. 

- `search_show`: Searches TMDB for a TV show and returns its show ID.
- `get_watch_time`: Gets episode counts and total watch time using TMDB data.
- `plan_binge_schedule`: Creates a day-by-day watching schedule based on the user's deadline and weekday/weekend availability. This is the original tool created for this project.

## Setup

BingeWise is deployed on Google Cloud Run and can be accessed directly through the deployed URL. The following steps are only needed to run the project locally:
1. Set a TMDB API key as an environment variable: 'export TMDB_API_KEY="YOUR_TMDB_API_KEY"
' 
2. Run the application: 'uv run app.py'
3. Open `http://localhost:8000` in your browser.

## Sample Grader Queries 

1. Can I finish Stranger Things before October 31, 2026? I can watch 2 hours on weekdays and 4 hours on weekends.
2. Can I finish Game of Thrones season 1 before November 1, 2026? I can watch 2 hours on weekdays and 4 hours on weekends.
3. Start with: "Can I finish The Office before November 10, 2026? I can watch 2 hours on weekdays and 3 hours on weekends." Then follow up in the same session: "What if I can watch 3 hours on weekdays instead?"