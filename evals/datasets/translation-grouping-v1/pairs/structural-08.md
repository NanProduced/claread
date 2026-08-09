# Blind grouping comparison — structural-08

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] 9.

[2] Methods

[3] 9.1.

[4] Overview

[5] The request method token is the primary source of request semantics;
   it indicates the purpose for which the client has made this request
   and what is expected by the client as a successful result.

[6] The request method's semantics might be further specialized by the
   semantics of some header fields when present in a request if those
   additional semantics do not conflict with the method.

[7] For example, a
   client can send conditional request header fields (Section 13.1) to
   make the requested action conditional on the current state of the
   target resource.

[8] HTTP is designed to be usable as an interface to distributed object
   systems.

[9] The request method invokes an action to be applied to a
   target resource in much the same way that a remote method invocation
   can be sent to an identified object.

[10] method = token

[11] The method token is case-sensitive because it might be used as a
   gateway to object-based systems with case-sensitive method names.

[12] By
   convention, standardized methods are defined in all-uppercase US-
   ASCII letters.

[13] Unlike distributed objects, the standardized request methods in HTTP
   are not resource-specific, since uniform interfaces provide for
   better visibility and reuse in network-based systems [REST].

[14] Once
   defined, a standardized method ought to have the same semantics when
   applied to any resource, though each resource determines for itself
   whether those semantics are implemented or allowed.

[15] This specification defines a number of standardized methods that are
   commonly used in HTTP, as outlined by the following table.

[16] +=========+============================================+=========+
   | Method  | Description                                | Section |
   | Name    |                                            |         |
   +=========+============================================+=========+
   | GET     | Transfer a current representation of the   | 9.3.1   |
   |         | target resource.

[17] |         |
   +---------+--------------------------------------------+---------+
   | HEAD    | Same as GET, but do not transfer the       | 9.3.2   |
   |

[18] | response content.

[19] |         |
   +---------+--------------------------------------------+---------+
   | POST    | Perform resource-specific processing on    | 9.3.3   |
   |         | the request content.

[20] |         |
   +---------+--------------------------------------------+---------+
   | PUT     | Replace all current representations of the | 9.3.4   |
   |         | target resource with the request content.

[21] |         |
   +---------+--------------------------------------------+---------+
   | DELETE  | Remove all current representations of the  | 9.3.5   |
   |         | target resource.

[22] |         |
   +---------+--------------------------------------------+---------+
   | CONNECT

[23] | Establish a tunnel to the server           | 9.3.6   |

[24] |         | identified by the target resource.

[25] |         |
   +---------+--------------------------------------------+---------+
   | OPTIONS | Describe the communication options for the | 9.3.7   |
   |         | target resource.

[26] |         |
   +---------+--------------------------------------------+---------+
   | TRACE   | Perform a message loop-back test along the | 9.3.8   |
   |         | path to the target resource.

[27] |         |
   +---------+--------------------------------------------+---------+

[28] Table 4

[29] All general-purpose servers MUST support the methods GET and HEAD.

[30] All other methods are OPTIONAL.

[31] The set of methods allowed by a target resource can be listed in an
   Allow header field (Section 10.2.1).

[32] However, the set of allowed
   methods can change dynamically.

[33] An origin server that receives a
   request method that is unrecognized or not implemented SHOULD respond
   with the 501 (Not Implemented) status code.

[34] An origin server that
   receives a request method that is recognized and implemented, but not
   allowed for the target resource, SHOULD respond with the 405 (Method
   Not Allowed) status code.

[35] Additional methods, outside the scope of this specification, have
   been specified for use in HTTP.

[36] All such methods ought to be
   registered within the "Hypertext Transfer Protocol (HTTP) Method
   Registry", as described in Section 16.1.

[37] 9.2.

[38] Common Method Properties

[39] 9.2.1.

[40] Safe Methods

[41] Request methods are considered "safe" if their defined semantics are
   essentially read-only; i.e., the client does not request, and does
   not expect, any state change on the origin server as a result of
   applying a safe method to a target resource.

[42] Likewise, reasonable
   use of a safe method is not expected to cause any harm, loss of
   property, or unusual burden on the origin server.

[43] This definition of safe methods does not prevent an implementation
   from including behavior that is potentially harmful, that is not
   entirely read-only, or that causes side effects while invoking a safe
   method.

[44] What is important, however, is that the client did not
   request that additional behavior and cannot be held accountable for
   it.

[45] For example, most servers append request information to access
   log files at the completion of every response, regardless of the
   method, and that is considered safe even though the log storage might
   become full and cause the server to fail.

[46] Likewise, a safe request
   initiated by selecting an advertisement on the Web will often have
   the side effect of charging an advertising account.

[47] Of the request methods defined by this specification, the GET, HEAD,
   OPTIONS, and TRACE methods are defined to be safe.

[48] The purpose of distinguishing between safe and unsafe methods is to
   allow automated retrieval processes (spiders) and cache performance
   optimization (pre-fetching) to work without fear of causing harm.

[49] In
   addition, it allows a user agent to apply appropriate constraints on
   the automated use of unsafe methods when processing potentially
   untrusted content.

[50] A user agent SHOULD distinguish between safe and unsafe methods when
   presenting potential actions to a user, such that the user can be
   made aware of an unsafe action before it is requested.

[51] When a resource is constructed such that parameters within the target
   URI have the effect of selecting an action, it is the resource
   owner's responsibility to ensure that the action is consistent with
   the request method semantics.

[52] For example, it is common for Web-
   based content editing software to use actions within query
   parameters, such as "page?do=delete".

[53] If the purpose of such a
   resource is to perform an unsafe action, then the resource owner MUST
   disable or disallow that action when it is accessed using a safe
   request method.

[54] Failure to do so will result in unfortunate side
   effects when automated processes perform a GET on every URI reference
   for the sake of link maintenance, pre-fetching, building a search
   index, etc.

[55] 9.2.2.

[56] Idempotent Methods

[57] A request method is considered "idempotent" if the intended effect on
   the server of multiple identical requests with that method is the
   same as the effect for a single such request.

[58] Of the request methods
   defined by this specification, PUT, DELETE, and safe request methods
   are idempotent.

[59] Like the definition of safe, the idempotent property only applies to
   what has been requested by the user; a server is free to log each
   request separately, retain a revision control history, or implement
   other non-idempotent side effects for each idempotent request.

[60] Idempotent methods are distinguished because the request can be
   repeated automatically if a communication failure occurs before the
   client is able to read the server's response.

[61] For example, if a
   client sends a PUT request and the underlying connection is closed
   before any response is received, then the client can establish a new
   connection and retry the idempotent request.

[62] It knows that repeating
   the request will have the same intended effect, even if the original
   request succeeded, though the response might differ.

[63] A client SHOULD NOT automatically retry a request with a non-
   idempotent method unless it has some means to know that the request
   semantics are actually idempotent, regardless of the method, or some
   means to detect that the original request was never applied.

[64] For example, a user agent can repeat a POST request automatically if
   it knows (through design or configuration) that the request is safe
   for that resource.

[65] Likewise, a user agent designed specifically to
   operate on a version control repository might be able to recover from
   partial failure conditions by checking the target resource
   revision(s) after a failed connection, reverting or fixing any
   changes that were partially applied, and then automatically retrying
   the requests that failed.

[66] Some clients take a riskier approach and attempt to guess when an
   automatic retry is possible.

[67] For example, a client might
   automatically retry a POST request if the underlying transport
   connection closed before any part of a response is received,
   particularly if an idle persistent connection was used.

[68] A proxy MUST NOT automatically retry non-idempotent requests.

[69] A
   client SHOULD NOT automatically retry a failed automatic retry.

[70] 9.2.3.

[71] Methods and Caching

[72] For a cache to store and use a response, the associated method needs
   to explicitly allow caching and to detail under what conditions a
   response can be used to satisfy subsequent requests; a method
   definition that does not do so cannot be cached.

[73] For additional
   requirements see [CACHING].

[74] This specification defines caching semantics for GET, HEAD, and POST,
   although the overwhelming majority of cache implementations only
   support GET and HEAD.

[75] 9.3.

[76] Method Definitions

## Grouping X

  G1: sentences 1–2 (2 sentences)
  G2: sentences 3–4 (2 sentences)
  G3: sentences 5–7 (3 sentences)
  G4: sentences 8–9 (2 sentences)
  G5: sentences 10 (1 sentence)
  G6: sentences 11–12 (2 sentences)
  G7: sentences 13–14 (2 sentences)
  G8: sentences 15 (1 sentence)
  G9: sentences 16 (1 sentence)
  G10: sentences 17–18 (2 sentences)
  G11: sentences 19 (1 sentence)
  G12: sentences 20 (1 sentence)
  G13: sentences 21 (1 sentence)
  G14: sentences 22–24 (3 sentences)
  G15: sentences 25 (1 sentence)
  G16: sentences 26–27 (2 sentences)
  G17: sentences 28 (1 sentence)
  G18: sentences 29–30 (2 sentences)
  G19: sentences 31–32 (2 sentences)
  G20: sentences 33–34 (2 sentences)
  G21: sentences 35–36 (2 sentences)
  G22: sentences 37–38 (2 sentences)
  G23: sentences 39–40 (2 sentences)
  G24: sentences 41–42 (2 sentences)
  G25: sentences 43–44 (2 sentences)
  G26: sentences 45–46 (2 sentences)
  G27: sentences 47 (1 sentence)
  G28: sentences 48–49 (2 sentences)
  G29: sentences 50 (1 sentence)
  G30: sentences 51–54 (4 sentences)
  G31: sentences 55–56 (2 sentences)
  G32: sentences 57–58 (2 sentences)
  G33: sentences 59 (1 sentence)
  G34: sentences 60–62 (3 sentences)
  G35: sentences 63–65 (3 sentences)
  G36: sentences 66–67 (2 sentences)
  G37: sentences 68–69 (2 sentences)
  G38: sentences 70–71 (2 sentences)
  G39: sentences 72–74 (3 sentences)
  G40: sentences 75–76 (2 sentences)

## Grouping Y

  G1: sentences 1–2 (2 sentences)
  G2: sentences 3–4 (2 sentences)
  G3: sentences 5 (1 sentence)
  G4: sentences 6–7 (2 sentences)
  G5: sentences 8–9 (2 sentences)
  G6: sentences 10 (1 sentence)
  G7: sentences 11–12 (2 sentences)
  G8: sentences 13–14 (2 sentences)
  G9: sentences 15 (1 sentence)
  G10: sentences 16–18 (3 sentences)
  G11: sentences 19–21 (3 sentences)
  G12: sentences 22–24 (3 sentences)
  G13: sentences 25–27 (3 sentences)
  G14: sentences 28 (1 sentence)
  G15: sentences 29–30 (2 sentences)
  G16: sentences 31–33 (3 sentences)
  G17: sentences 34 (1 sentence)
  G18: sentences 35–36 (2 sentences)
  G19: sentences 37–38 (2 sentences)
  G20: sentences 39–40 (2 sentences)
  G21: sentences 41–42 (2 sentences)
  G22: sentences 43–45 (3 sentences)
  G23: sentences 46 (1 sentence)
  G24: sentences 47 (1 sentence)
  G25: sentences 48–49 (2 sentences)
  G26: sentences 50 (1 sentence)
  G27: sentences 51–53 (3 sentences)
  G28: sentences 54 (1 sentence)
  G29: sentences 55–56 (2 sentences)
  G30: sentences 57–58 (2 sentences)
  G31: sentences 59 (1 sentence)
  G32: sentences 60–62 (3 sentences)
  G33: sentences 63 (1 sentence)
  G34: sentences 64–65 (2 sentences)
  G35: sentences 66–67 (2 sentences)
  G36: sentences 68–69 (2 sentences)
  G37: sentences 70–71 (2 sentences)
  G38: sentences 72–73 (2 sentences)
  G39: sentences 74 (1 sentence)
  G40: sentences 75–76 (2 sentences)
