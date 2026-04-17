# BPE Macro Mining Summary

- Trace run dir: `/Users/abhayp/Documents/browser_use/browser_traces/amazon_single_item_purchase_30_p004/run_001`
- Episodes parsed: `28`
- Primitive action tokens: `348`
- BPE merges applied: `31`
- Macros emitted: `14`

## Top macros

### macro_001_navigate_input_click
- Support: `18`
- Sequence length: `3`
- Estimated saved calls: `36`
- Description: Reusable action chain (navigate -> input -> click) observed 18 times. Estimated primitive-call reduction if used as one macro: 36.
- Sequence: `navigate(new_tab,url) -> input(clear,index,text) -> click(index)`

### macro_005_navigate_input_click
- Support: `12`
- Sequence length: `4`
- Estimated saved calls: `36`
- Description: Reusable action chain (navigate -> input -> click -> wait) observed 12 times. Estimated primitive-call reduction if used as one macro: 36.
- Sequence: `navigate(new_tab,url) -> input(clear,index,text) -> click(index) -> wait(seconds)`

### macro_010_write_file_navigate_replace_file
- Support: `6`
- Sequence length: `6`
- Estimated saved calls: `30`
- Description: Reusable action chain (write_file -> navigate -> replace_file -> input -> click -> replace_file) observed 6 times. Estimated primitive-call reduction if used as one macro: 30.
- Sequence: `write_file(append,content,file_name,leading_newline,trailing_newline) -> navigate(new_tab,url) -> replace_file(file_name,new_str,old_str) -> input(clear,index,text) -> click(index) -> replace_file(file_name,new_str,old_str)`

### macro_008_click_click_done
- Support: `12`
- Sequence length: `3`
- Estimated saved calls: `24`
- Description: Reusable action chain (click -> click -> done) observed 12 times. Estimated primitive-call reduction if used as one macro: 24.
- Sequence: `click(index) -> click(index) -> done(files_to_display,success,text)`

### macro_013_navigate_input_click
- Support: `3`
- Sequence length: `9`
- Estimated saved calls: `24`
- Description: Reusable action chain (navigate -> input -> click -> wait -> click -> wait -> click -> click -> done) observed 3 times. Estimated primitive-call reduction if used as one macro: 24.
- Sequence: `navigate(new_tab,url) -> input(clear,index,text) -> click(index) -> wait(seconds) -> click(index) -> wait(seconds) -> click(index) -> click(index) -> done(files_to_display,success,text)`

### macro_011_wait_scroll_replace_file
- Support: `3`
- Sequence length: `8`
- Estimated saved calls: `21`
- Description: Reusable action chain (wait -> scroll -> replace_file -> click -> replace_file -> click -> replace_file -> click) observed 3 times. Estimated primitive-call reduction if used as one macro: 21.
- Sequence: `wait(seconds) -> scroll(down,pages) -> replace_file(file_name,new_str,old_str) -> click(index) -> replace_file(file_name,new_str,old_str) -> click(index) -> replace_file(file_name,new_str,old_str) -> click(index)`

### macro_009_write_file_navigate_replace_file
- Support: `3`
- Sequence length: `7`
- Estimated saved calls: `18`
- Description: Reusable action chain (write_file -> navigate -> replace_file -> input -> click -> replace_file -> scroll) observed 3 times. Estimated primitive-call reduction if used as one macro: 18.
- Sequence: `write_file(append,content,file_name,leading_newline,trailing_newline) -> navigate(new_tab,url) -> replace_file(file_name,new_str,old_str) -> input(clear,index,text) -> click(index) -> replace_file(file_name,new_str,old_str) -> scroll(down,pages)`

### macro_014_replace_file_click_replace_file
- Support: `3`
- Sequence length: `7`
- Estimated saved calls: `18`
- Description: Reusable action chain (replace_file -> click -> replace_file -> click -> replace_file -> done -> done) observed 3 times. Estimated primitive-call reduction if used as one macro: 18.
- Sequence: `replace_file(file_name,new_str,old_str) -> click(index) -> replace_file(file_name,new_str,old_str) -> click(index) -> replace_file(file_name,new_str,old_str) -> done(files_to_display,success,text) -> done(files_to_display,success,text)`

### macro_007_navigate_input_click
- Support: `3`
- Sequence length: `6`
- Estimated saved calls: `15`
- Description: Reusable action chain (navigate -> input -> click -> wait -> click -> click) observed 3 times. Estimated primitive-call reduction if used as one macro: 15.
- Sequence: `navigate(new_tab,url) -> input(clear,index,text) -> click(index) -> wait(seconds) -> click(index) -> click(index)`

### macro_012_replace_file_replace_file_click
- Support: `3`
- Sequence length: `6`
- Estimated saved calls: `15`
- Description: Reusable action chain (replace_file -> replace_file -> click -> replace_file -> click -> done) observed 3 times. Estimated primitive-call reduction if used as one macro: 15.
- Sequence: `replace_file(file_name,new_str,old_str) -> replace_file(file_name,new_str,old_str) -> click(index) -> replace_file(file_name,new_str,old_str) -> click(index) -> done(files_to_display,success,text)`

## Compression

- Tokens before merges: `348`
- Tokens after merges: `101`
- Compression ratio: `3.446`
