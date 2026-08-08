# Assignment 2: Compare & Contrast — Dual Implementation of an Expense Approval Workflow

**Name:** Todd O'Neil  
**Student Number:** 040573645  
**Course:** CST8917 - Serverless Applications | Spring/Summer 2026  
**Project Title:** Expense Approval Pipeline — Durable Functions vs. Logic Apps + Service Bus
**Date:** August 08, 2026

---

## Table of Contents

1. [Demo Link](#demo-link)
2. [Version A Summary — Durable Functions](#version-a-summary--durable-functions)
3. [Version B Summary — Logic Apps + Service Bus](#version-b-summary--logic-apps--service-bus)
4. [Comparison Analysis](#comparison-analysis)
5. [Recommendation](#recommendation)
6. [References](#references)
7. [AI Disclosure](#ai-disclosure)

---

## [Demo Link](https://www.youtube.com/watch?v=goBTUqiQ1PM)

## Version A Summary — Durable Functions

Version A was implemented with an HTTP triggered Durable Function that uses the Table storage API to store expenses for auditing and leveraging Logic Apps for notifications.

**Architecture:**

The client function validates the JSON body i nthe request before starting the orchestrator function saving resources.  The orchestrator using activity chaining to process the expense in a linear fashion validating the category -> storing expense -> amount business logic -> determining human interation -> handing off to logic app for notifications.

**Design Decisions:**  

- Validating JSON in the client function saves time rather than validations happening in the orchestrator allowing for early returns.
- I decided to use function chaining rather than fan-out fan-in due to the linear nature of validations. Running the amount business logic doesn't make sense if the caegory isn't valid.
- I chose Logic Apps for the notification for its ease of use in simple workflows. It is only used to send an email to the used based on the status sent in the payload.

**Challenges:**  

- Configuring the human interaction pattern was a challenge, as I have never done that before.  Using Microsoft's exmaple [here](https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-human-interaction?tabs=python&pivots=durable-functions) I was able to adapt that to my use case.
- Inserting and updating records with the table storage API proved a little annoying. Date value types aren't allowed as they expected to be strings. Easy enough fix with str(), ToString(), etc. But when you're getting errors without great context is makes things difficult.

**Test Results (`test-durable.http`):**

| # | Scenario | Expected Outcome | Result |
|---|----------|-------------------|--------|
| 1 | Valid expense under $100 | Auto-approved | [Pass] |
| 2 | Valid expense ≥ $100, manager approves | Approved | [Pass] |
| 3 | Valid expense ≥ $100, manager rejects | Rejected | [Pass] |
| 4 | Valid expense ≥ $100, no manager response | Escalated | [Pass] |
| 5 | Missing required fields | Validation error | [Pass] |
| 6 | Invalid category | Validation error | [Pass] |

---

## Version B Summary — Logic Apps + Service Bus

Version B implementation uses 2 different Logic Apps with Azure Service Bus between them.  This allows for decoupling and making the overall workflow easier to implement.

**Architecture:** 

Similar to Version A, a user submits an expense via HTTP request. That is sent to the Injection Logic app which handles JSON validation, category validation, record storage, and expense amount business logic.  It then sends a message to Service Bus with the storage record metadata for the processing Logic App.

The processing Logic app reads the message from Service Bus and gets the record from table storage.  Based on the type it will either send an auto-approved email to the user or will send a verification request email to the manager.  If the manager email timesout an escalated email is sent to the user, otherwise an approved/rejected email is sent to the user.

**Approach chosen for manager approval:**  

Since Logic Apps doesn't inherantly have a "human interaction" pattern like Durable Functions I decided to use the Office365 Approval Email connection.  This allows for an easy implementation of this pattern. It automatically send an email with buttons for the choices you are looking for, such as "approved" / "rejected" and waits for a response. I than had parallel branches, one for when a response was given and another for when the email response timed out.  Both branches execute the same logic just with different values -> update the record in table storage and send a notification email.

**Design Decisions:**
- I split this application into two different logic apps to make the development easier. Each application is responsible for one half of the logic, seperating concerns. This also makes editing/changing each part of the application simpler as each one is isolated.
- Service Bus Queues were used instead of topics.  Topic would have been the better choice if multiple services needed to process the message information. Since this scenario was limited in scope a messaging queue with a peek-lock for individual processing made the most sense.

**Challenges:**
- Implementing the close message functionality when the processing logic app was finished and emails were sent was problematic. If messages are not cleaning closed after processing the application continously processes the same message over again forever.  Adding the Close message action resolves this issue.
- The Office365 Approval-Email action was causing an issue with my initial configuration of the message queue. The message locks were only set for 1 minute, so with the timeout of the approval email also being a minute, once the emailed timed out and executed the parallel branch to process the 'Escalated' workflow the message lock had expired and woudl throw a 400 error.  Increasing the lock timer on the messaging queue resolved this issue.

**Test Results / Screenshots:**  
Testing data structure(emails are placehoders and were not used in tests):  
```json 
{
    "employeeName": "Jane Doe",
    "employeeEmail": "test@example.com",
    "amount": 101,
    "category": "supplies",
    "description": "Office supplies - notebooks and pens",
    "managerEmail": "test@example.com"
}
```
Version B Injestion
![Intake Success](/version-b-logic-app/screenshots/version-b-demo-intake-success.png)

Version B Processing
![Processing Success](/version-b-logic-app/screenshots/version-b-demo-process-success.png)

---

## Comparison Analysis
### Development Experience

My experience developing both applications was good overall. Durable Functions I found to be far easier to implement the business logic and "heavy lifting" of the workflow.  With local development and the ability to use breakpoints for debugging I was more confident I had the logic correct using code than in something like Logic Apps.

Logic Apps had it's advantages as well though. Having a visual designer plus pre-built templates for common use-cases makes for an easy and enjoyable experience. If a workflow fits within any of the available templates Logic Apps could be a clear winner and obvious choice.  Having a wide selection on enterprise connectors really simplfies integration, so I cetainly give the edge to Logic Apps in that regard. being able to seeemlessly and simply integrate connections to different resources, be it Azure or thrid party, really made the notification development a breeze.

### Testability

Testing both applications was fairly easy.  Logic Apps has the abolity to run the application with or without a payload.  The drawback was I didn't find any way to pre-define certain situations, like creating pre-built payloads for different tests.  However it made up for that in the visual display while the app was running.

Durable Functions required a bit more setup for testing, but once that initial configuration was set it made subsequent tests much faster and easier. Using a .http file with pre-detrmined scenarios and payloads made ensuring wider testing coverage could be achieved.

Although I didn't utilize any, Durable Functions allow for standard testing libraries for any of its runtimes.  This can make development cleaner since you could take a TDD approach while developing the application.  Due to the different options available for Functions of Logic Apps my experience was Durable Functions have a much better environment for testing your application and making sure it's correct before deployment.

### Error Handling

Using Durable Functions, you handle errors the same way you would in any application code. Using catch blocks and exceptions give you full control over when to throw runtime errors and how to handle them when errors do arise.  That said it is up to the developer to properly handle errors, and if not done correctly it can cause problems or unexpected results.

Logic Apps hand errors differently, the developer is stil responsible for runtime errors or failures, but they're done through parallel branchs that trigger on failure/timeouts. The other option is to use condition actions, but that can get very messy very quickly.  

One drawback to error handling in Logic Apps is the execute on run status dropdown menu only allows to check actions on the same level of the selected action. So if you have an action you'd like to run outside of a condition, and you want to check the run status of an action inside that condition you won't be able to unless you utilize code. If the developer isn't familiar with the programming syntax required they won't be able to access the action they'd like to track.

One error I ran into was a 400 BadRequest error when the timout workflow exectued in Version B. This was due to the message lock not being long enough (it defaults to 1 minute). So when the message was read the timer started and the human interaction also had a timeout of 1 minute so when it expired and triggered the timeout branch, by the time it reached the close message action the lock had expired.

### Human Interaction Pattern

The Human interaction pattern for Version A was accomplished by setting a durable timer for one minute and using context.await_external_event('external_event') to set an event task.  Once those two were set you would "race" them with context.task_any([external_event, timer]), which ever one respolved first would determine the rest of the workflow. If the timer resolved first the expense would be escalated, if the manager response resloved first the record would be updated in storage and a notificatoin would be sent to the user with the response.

Version B proved to be simpler to implement, but far more limited. Using the Office365 Approval Email action sends an email with two buttons "approved" and "rejected". The response from the recipient is tracked in the response of the logic app action.  A timeout duratoin is also able to be set.  The drawback to this approve while easy to use, the response data is very limited. It's a yes or no choice. So if you required something like a reason why the expense was either approved or rejected, the Approval Email action wouldn't be suited for that requirement.

### Observability

Both Version A and Version B come equipped with some version of runtime history. Durable Fucntions have invocations, the ability to track which function has been called, how many successes and how many failures. However, this doesn't come out of the box. Azure Application Insights is required to be setup with the function app in order to use this feature.

Logic Apps have run history.  Each time the app is trigger it's current running status is tracked, along with when it finishes whether it was a success or a failure.  Another feaure I really likes with Version B was the built-in build history. Everytime you saved the logic app it would get its own version, which makes it easier to track if a modification you made broke something.

### Cost
| Volume | Version A (Durable Functions) | Version B (Logic Apps + Service Bus) |
|--------|-------------------------------|----------------------------------------|
| ~100 expenses/day | $[0]/mo | $[0]/mo |
| ~10,000 expenses/day | $[0]/mo | $[0.17]/mo |


---

## Recommendation

My recommendation if this application were to be built in production would be to use both Durable Functions and Logic Apps, playing to each one's strengths rather than picking a single tool.

I'd use Durable Functions to handle the heavy lifting and complex business logic, and let Logic Apps manage storage, notifications, and integrations through its built-in connectors. Taking a code-first approach for the processing logic lets me use existing libraries in whatever runtime I choose, which makes implementing business logic far less tedious and easier to unit test than the same logic buried in a Logic App's designer.

In practice, I found Logic Apps aren't well suited to workflows with more than a couple of conditional branches. Once I added a few nested conditions, the designer became hard to read and even harder to debug — I was clicking through collapsed panes just to trace a single execution path. Durable Functions handled that same branching logic far more cleanly in code, with real breakpoints and stack traces when something failed.

Where Logic Apps earned its keep was orchestrating external services. The built-in connectors saved real time versus writing and maintaining that integration code by hand.

If I were building something with simple, mostly linear logic and heavy reliance on existing connectors, something like a basic approval workflow, I'd lean on Logic Apps alone and skip Durable Functions entirely. But for anything with real branching or business rules, I'd default to Durable Functions first and only reach for Logic Apps where its connectors genuinely save effort.

---

## References

- Microsoft Human Interaction Example: https://learn.microsoft.com/en-us/azure/durable-task/common/durable-task-human-interaction?tabs=python&pivots=durable-functions
- Azure Pricing Calculator: https://azure.microsoft.com/pricing/calculator/
- Google: https://google.com

---

# AI Disclosure
Claude AI was used to help with parsing error logs.  
No AI was used in code generation.  
AI was used to generate a template format of this README document, but was not used for the substance/content of this document.