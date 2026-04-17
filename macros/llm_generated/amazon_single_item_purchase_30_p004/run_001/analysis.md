# LLM Macro Mining Report

- Generated at: `2026-04-17T04:15:32.947785Z`
- Traces analyzed: `28`
- Macros generated: `5`
- Baseline calls: `348`
- Projected calls: `281`
- Estimated saved calls: `67`
- Estimated reduction: `19.253%`

## Macro docs

### amazon_search
- Description: Navigate to Amazon and perform a product search by entering a query into the search box and clicking the search button.
- When to use: At the start of any Amazon shopping task when you need to find a product by keyword search.
- One-shot example: amazon_search(query="wireless Bluetooth headset noise cancellation")
- Action pattern: `navigate -> input -> click`

### select_product_and_add_to_cart
- Description: From Amazon search results, click on a product listing to open its detail page, then click the Add to Cart button on the product page.
- When to use: After viewing Amazon search results and identifying a suitable product, use this to open the product and add it to cart in one step.
- One-shot example: select_product_and_add_to_cart(product_index=10615, add_to_cart_index=9612)
- Action pattern: `click -> click`

### add_to_cart_and_proceed_to_checkout
- Description: After viewing a product detail page, click Add to Cart and then click the Proceed to Checkout button on the cart confirmation page.
- When to use: When you are on an Amazon product page and want to add the item to cart and immediately proceed to checkout in one macro call.
- One-shot example: add_to_cart_and_proceed_to_checkout(add_to_cart_index=26898, checkout_index=49017)
- Action pattern: `click -> click`

### init_todo_and_navigate
- Description: Create a todo.md tracking file for the task and then navigate to the target website. Used by the agent to maintain structured progress tracking.
- When to use: At the very beginning of a shopping task when the agent wants to create a progress-tracking file before starting the browsing workflow.
- One-shot example: init_todo_and_navigate(todo_content="# Amazon Purchase Task\n...\n- [ ] Navigate to Amazon.com\n...", url="https://www.amazon.com")
- Action pattern: `write_file -> navigate`

### search_wait_and_scroll
- Description: After submitting a search query, wait for results to load and optionally scroll down to see more product listings before selecting one.
- When to use: Immediately after clicking the search button on Amazon, to ensure results are fully loaded and to reveal more product options below the fold.
- One-shot example: search_wait_and_scroll(wait_seconds=3, scroll_pages=1.0)
- Action pattern: `wait -> scroll`
