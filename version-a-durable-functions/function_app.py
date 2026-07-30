import json
import azure.functions as func
import logging
from typing import Dict, Any
import datetime

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)



# --- Helper Functions ---
def UpdatePayload(payload: Dict[str, Any], status: str, reason: str):
    payload['response']['status'] = status
    payload['response']['reason'] = reason

    return payload


# An HTTP-Triggered Function with a Durable Functions Client binding
@app.route(route="orchestrators/{functionName}")
@app.durable_client_input(client_name="client")
async def expense_orchestration_starter(req: func.HttpRequest, client):

    logging.info("check-booking function triggered")

    try:
        expense = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            mimetype="application/json",
            status_code=400
        )

    # Validate required fields
    required_fields = ["employeeName", "amount", "employeeEmail", "category", "description", "managerEmail"]
    missing = [field for field in required_fields if field not in expense]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Missing required fields: {', '.join(missing)}"}),
            mimetype="application/json",
            status_code=400
        )

    function_name = req.route_params.get('functionName')
    instance_id = await client.start_new(
        function_name,
        client_input=expense)
    response = client.create_check_status_response(req, instance_id)

    return response


# Orchestrator
@app.orchestration_trigger(context_name="context")
def expense_orchestrator(context):

    skip_processing = False

    data = context.get_input()

    # --- Step 1: Validate ---
    validation_result = yield context.call_activity(
        "validate_category",
        data['category']
    )

    final_response = {
        'Name': data['employeeName'],
        'Email': data['employeeEmail'],
        'amount': data['amount'],
        'category': data['category'],
        'description': data['description'],
        'manager_email': data['managerEmail'],
        'response': {
            'status': '',
            'reason': ''
        }
    }

    if not validation_result['is_valid']:
        final_response = UpdatePayload(
            final_response, 
            'Rejected', 
            validation_result['reason'])
        skip_processing = True

    if not skip_processing:    
        # --- Step 2: Check Auto Approval ---


        # --- Step 3: Request Manager Approval ---
        approval_event = "manager_approval"
        timeout = context.current_utc_datetime + datetime.timedelta(seconds=60)

        # Creates a task to listen for a 'manager-approval' event triggered by a person
        approval_task = context.wait_for_external_event(approval_event)

        # Creates the timeout task setting the durable function timer to the defined 60 seconds above
        timeout_task = context.create_timer(timeout)

        # Races the 2 tasks defined above, which ever task responds first will decide processing logic
        winner = yield context.task_any([approval_task, timeout_task])

        if winner == timeout_task:
            final_response = UpdatePayload(
                final_response, 
                'Escalated', 
                'Manager unavailable for approval.')
        else:
            manager_decision = approval_task.result
            final_response = UpdatePayload(
                final_response, 
                manager_decision['decision'], 
                manager_decision['reason'])
                


    # --- Step 4: Notify User ---
    notification_result = yield context.call_activity(
        "send_notification",
        final_response
    )

    return {
        "status": "Approved",
        "processing": processing_result,
        "notification": notification_result
    }


# Activity
# --- Send Notification ---
@app.activity_trigger(input_name="city")
def send_notification(city: str):
    return "Hello " + city 


# Activity
# --- Category Validiation ---
@app.activity_trigger(input_name="category")
def validate_category(category: str):

    # Validate Category
    valid_categories = ["travel", "meals", "supplies", "equipment", "software", "other"]
    valid = category in valid_categories   

    return {
        'is_valid': valid,
        'reason': f"{category} is not a valid category"
    }

# Activity
# --- Manager Approval Endpoint ---
@app.route(route="approval/{instance_id}")
@app.durable_client_input(client_name="client")
async def manager_approval(req: func.HttpRequest, client):
    """
    Manager approves or rejects an expense.
    Example:
      POST /approval/abc123
      body:{
        'decision': 'Approved',
        'reason': 'some reason given'
      }
    """
    try:
        approval = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON in request body"}),
            mimetype="application/json",
            status_code=400
        )

    required_fields = ['decision']
    missing = [field for field in required_fields if field not in approval]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Missing required fields: {', '.join(missing)}"}),
            mimetype="application/json",
            status_code=400
        )

    instance_id = req.route_params.get("instance_id")

    await client.raise_event(instance_id, "manager_approval", approval)

    return func.HttpResponse(
        f"Sent decision '{approval}' to instance {instance_id}"
    )

# --- HEALTH CHECK ---
@app.function_name(name="health")
@app.route(route="", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint."""
    return func.HttpResponse(
        json.dumps({"status": "healthy", "service": "Expense Approval Pipeline"}),
        mimetype="application/json",
        status_code=200
    )