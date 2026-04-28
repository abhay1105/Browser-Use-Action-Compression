# Browser-Use Cost Analysis (Dollar-Focused)

## Data Used
- Source files: `awo_browser_traces/yelp_search_50_p008.costs.json` and `awo_browser_traces/google_flights_48_p009.costs.json`
- Total runs analyzed: 80 (40 per task type)

## Key Per-Task Cost Takeaways
- Overall average per task: **$0.41**
- Overall median per task (typical): **$0.42**
- Observed range: **$0.08 to $1.04**
- Yelp average per task: **$0.21**
- Google Flights average per task: **$0.61**

## What This Means For Budgeting
Use these rough planning ranges from observed traces:
-   10 tasks: low $1.46, typical $4.18, high $7.34
-   50 tasks: low $7.31, typical $20.92, high $36.70
-  100 tasks: low $14.62, typical $41.84, high $73.40
-  500 tasks: low $73.09, typical $209.22, high $367.00
- 1000 tasks: low $146.17, typical $418.45, high $734.00

## Figures
- `01_average_cost_per_task.svg`: average cost with min-max ranges
- `02_cost_distribution_histogram.svg`: frequency of different per-task costs
- `03_budget_scaling_projection.svg`: total spend as task count grows
- `04_cost_components_breakdown.svg`: prompt vs completion cost components

## PNG Versions (Text Embedded)
- `01_average_cost_per_task.png`
- `02_cost_distribution_histogram.png`
- `03_budget_scaling_projection.png`
- `04_cost_components_breakdown.png`
