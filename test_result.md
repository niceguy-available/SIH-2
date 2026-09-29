#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Harden the Lunar image registration workspace (Moon Match Points). This session:
  (1) Add automatic coordinate-matched LROC reference retrieval with persistent cache reuse across sessions.
  (2) Complete the release-gate deliberate-failure test matrix and verify Emergent Object Storage.
  Reference DB: https://lroc.im-ldi.com/images/downloads/ (live). Philosophy: honest failure over a forced false match.

backend:
  - task: "Automatic coordinate-matched LROC reference retrieval + persistent cache (POST /api/catalog/auto-reference)"
    implemented: true
    working: "NA"
    file: "routers/catalog.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "New endpoint POST /api/catalog/auto-reference?latitude&longitude&prefer_cache. Matches local curated footprints (match_footprints helper), reuses persistent cache (references collection + object storage) when present (from_cache=true, no LROC request), else fetches from allowlisted LROC URL, stores to object storage, tags product_id, returns from_cache=false. Verified via curl: Tycho reuse (from_cache true), fresh fetch prefer_cache=false (from_cache false, new sha), north pole match, invalid coords 422. Needs independent verification incl. 404 (no match is impossible with global mosaics so 404 only via unknown; honest 503 on all-fail)."
  - task: "Deliberate-failure release-gate matrix (no-overlap, repeated terrain, severe shadow, longitude-wrap, known-transform, no-substitution)"
    implemented: true
    working: true
    file: "tests/test_deliberate_failures.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "New pytest module, 6/6 passing (-n0): known rotation(3deg)+scale(1.03) recovered rmse<1.5; unrelated scenes -> honest failure or gate/export blocked; repeated terrain -> not force-locked; severe shadow source -> failed with honest diagnostic; global geographic reference -> export 403; unknown product context -> 404 no substitution."
  - task: "Emergent Object Storage put/restore round-trip for uploads"
    implemented: true
    working: true
    file: "lib/storage.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        - working: true
          agent: "main"
          comment: "init_storage OK; put_file+restore_file round-trip verified with sha256 match. Uploads persisted to object storage (durable across sessions); pod-local file is scratch only."

frontend:
  - task: "Auto-fetch LROC reference by coordinates in workspace reference panel"
    implemented: true
    working: "NA"
    file: "src/components/ReferenceControls.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Added 'Auto-match from LROC' block with latitude/longitude inputs (data-testid auto-reference-latitude-input, auto-reference-longitude-input) and auto-fetch-reference-btn. On success invalidates references, selects returned ref, shows auto-reference-status (cache vs fresh). tsc clean; panel renders desktop+mobile with no overflow. Needs UI flow verification."

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 1
  run_ui: true

test_plan:
  current_focus:
    - "Automatic coordinate-matched LROC reference retrieval + persistent cache (POST /api/catalog/auto-reference)"
    - "Auto-fetch LROC reference by coordinates in workspace reference panel"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: "Please verify the new auto-reference feature end-to-end. Backend: POST /api/catalog/auto-reference (match, cache-reuse from_cache=true, fresh fetch via prefer_cache=false from_cache=false, invalid coords 422). Frontend: on the workspace, enter latitude -43.31 and longitude -11.36 in the Auto-match from LROC inputs, click Auto-fetch reference (auto-fetch-reference-btn); a reference should get selected in reference-catalog-select and auto-reference-status should appear. Do NOT run destructive tests that wipe the references collection. The deliberate-failure pytest module already passes (backend/tests/test_deliberate_failures.py) and object storage is verified; re-run only if convenient."