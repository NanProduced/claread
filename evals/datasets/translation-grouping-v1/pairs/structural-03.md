# Blind grouping comparison — structural-03

You will judge two alternative partitions of the same article into
translation groups. The article's sentences are numbered below.
Read the article first, then evaluate both groupings against the rubric.

## Article sentences

[1] ## Making a request

[2] To make a request, call `fetch()`, passing in:

[3] 1. a definition of the resource to fetch.

[4] This can be any one of:
   - a string containing the URL
   - an object, such as an instance of {{domxref("URL")}}, which has a {{glossary("stringifier")}} that produces a string containing the URL
   - a {{domxref("Request")}} instance
2.

[5] optionally, an object containing options to configure the request.

[6] In this section we'll look at some of the most commonly-used options.

[7] To read about all the options that can be given, see the [`fetch()`](/en-US/docs/Web/API/Window/fetch) reference page.

[8] ### Setting the method

[9] By default, `fetch()` makes a {{httpmethod("GET")}} request,

[10] but you can use the `method` option to use a different [request method](/en-US/docs/Web/HTTP/Reference/Methods):

[11] ```js
const response = await fetch("https:

[12] //example.org/post", {
  method:

[13] "POST",
  // …
});

[14] ```

[15] If the `mode` option is set to `no-cors`, then `method` must be one of `GET`, `POST` or `HEAD`.

[16] ### Setting a body

[17] The request body is the payload of the request: it's the thing the client is sending to the server.

[18] You cannot include a body with `GET` requests, but it's useful for requests that send content to the server, such as {{httpmethod("POST")}} or {{httpmethod("PUT")}} requests.

[19] For example, if you want to upload a file to the server, you might make a `POST` request and include the file as the request body.

[20] To set a request body, pass it as the `body` option:

[21] ```js
const response = await fetch("https:

[22] //example.org/post", {
  method:

[23] "POST",
  body:

[24] JSON.stringify({ username:

[25] "example" }),
  // …
});

[26] ```

[27] You can supply the body as an instance of any of the following types:

[28] - a string
- {{jsxref("ArrayBuffer")}}
- {{jsxref("TypedArray")}}
- {{jsxref("DataView")}}
- {{domxref("Blob")}}
- {{domxref("File")}}
- {{domxref("URLSearchParams")}}
- {{domxref("FormData")}}
- {{domxref("ReadableStream")}}

[29] Other objects are converted to strings using their `toString()` method.

[30] For example, you can use a {{domxref("URLSearchParams")}} object to encode form data (see [setting headers](#setting_headers) for more information):

[31] ```js
const response = await fetch("https:

[32] //example.org/post", {
  method:

[33] "POST",
  headers:

[34] {
    "Content-Type":

[35] "application/x-www-form-urlencoded",
  },
  // Automatically converted to "username=example&password=password"
  body:

[36] new URLSearchParams({ username:

[37] "example", password:

[38] "password" }),
  // …
});

[39] ```

[40] Note that just like response bodies, request bodies are streams,

[41] and making the request reads the stream,

[42] so if a request contains a body, you can't make it twice:

[43] ```js example-bad
const request = new Request("https:

[44] //example.org/post", {
  method:

[45] "POST",
  body:

[46] JSON.stringify({ username:

[47] "example" }),
});

[48] const response1 = await fetch(request);

[49] console.log(response1.status);

[50] // Will throw: "Body has already been consumed.

[51] "
const response2 = await fetch(request);
console.log(response2.status);
```

[52] Instead, you would need to {{domxref("Request.clone()", "create a clone", "", "nocode")}} of the request before sending it:

[53] ```js
const request1 = new Request("https:

[54] //example.org/post", {
  method:

[55] "POST",
  body:

[56] JSON.stringify({ username:

[57] "example" }),
});

[58] const request2 = request1.clone();

[59] const response1 = await fetch(request1);

[60] console.log(response1.status);

[61] const response2 = await fetch(request2);

[62] console.log(response2.status);

[63] ```

[64] See [Locked and disturbed streams](#locked_and_disturbed_streams) for more information.

[65] ### Setting headers

[66] Request headers give the server information about the request: for example, in a `POST` request, the {{httpheader("Content-Type")}} header tells the server the format of the request's body.

[67] To set request headers, assign them to the `headers` option.

[68] You can pass an object literal here containing `header-name:

[69] header-value` properties:

[70] ```js
const response = await fetch("https:

[71] //example.org/post", {
  method:

[72] "POST",
  headers:

[73] {
    "Content-Type":

[74] "application/json",
  },
  body:

[75] JSON.stringify({ username:

[76] "example" }),
  // …
});

[77] ```

[78] Alternatively, you can construct a {{domxref("Headers")}} object, add headers to that object using {{domxref("Headers.append()")}}, then assign the `Headers` object to the `headers` option:

[79] ```js
const myHeaders = new Headers();

[80] myHeaders.append("Content-Type", "application/json");

[81] const response = await fetch("https:

[82] //example.org/post", {
  method:

[83] "POST",
  headers:

[84] myHeaders,
  body:

[85] JSON.stringify({ username:

[86] "example" }),
  // …
});

[87] ```

[88] Compared to using plain objects, the `Headers` object provides some additional input sanitization.

[89] For example, it normalizes header names to lowercase, strips leading and trailing whitespace from header values, and prevents certain headers from being set.

[90] Many headers are set automatically by the browser and can't be set by a script: these are called {{glossary("Forbidden request header", "Forbidden request headers")}}.

[91] If the {{domxref("Request.mode", "mode")}} option is set to `no-cors`, then the set of permitted headers is further restricted.

[92] ### Sending data in a GET request

[93] `GET` requests don't have a body, but you can still send data to the server by appending it to the URL as a query string.

[94] This is a common way to send form data to the server.

[95] You can do this by using {{domxref("URLSearchParams")}} to encode the data, and then appending it to the URL:

[96] ```js
const params = new URLSearchParams();

[97] params.append("username", "example");

[98] // GET request sent to https:

[99] //example.org/login?username=example
const response = await fetch(`https:

[100] //example.org/login?${params}`);

[101] ```

[102] ### Making cross-origin requests

[103] Whether a request can be made cross-origin or not is determined by the value of the {{domxref("RequestInit", "", "mode")}} option.

[104] This may take one of three values: `cors`, `same-origin`, or `no-cors`.

[105] - For fetch requests the default value of `mode` is `cors`, meaning that if the request is cross-origin then it will use the [Cross-Origin Resource Sharing (CORS)](/en-US/docs/Web/HTTP/Guides/CORS) mechanism.

[106] This means that:
  - if the request is a [simple request](/en-US/docs/Web/HTTP/Guides/CORS#simple_requests), then the request will always be sent, but the server must respond with the correct {{httpheader("Access-Control-Allow-Origin")}} header or the browser will not share the response with the caller.
  - if the request is not a simple request, then the browser will send a [preflighted request](/en-US/docs/Web/HTTP/Guides/CORS#preflighted_requests) to check that the server understands CORS and allows the request, and the real request will not be sent unless the server responds to the preflighted request with the appropriate CORS headers.

[107] - Setting `mode` to `same-origin` disallows cross-origin requests completely.

[108] - Setting `mode` to `no-cors` disables CORS for cross-origin requests.

[109] This restricts the headers that may be set, and restricts methods to GET, HEAD, and POST.

[110] The response is _opaque_, meaning that its headers and body are not available to JavaScript.

[111] Most of the time a website should not use `no-cors`: the main application of it is for certain service worker use cases.

[112] See the reference documentation for {{domxref("RequestInit", "", "mode")}} for more details.

[113] ### Including credentials

[114] In the context of the Fetch API, a credential is an extra piece of data sent along with the request that the server may use to authenticate the user.

[115] All the following items are considered to be credentials:

[116] - HTTP cookies
- {{glossary("TLS")}} client certificates
- The {{httpheader("Authorization")}} and {{httpheader("Proxy-Authorization")}} headers.

[117] By default, credentials are only included in same-origin requests.

[118] To customize this behavior, as well as to control whether the browser respects any **`Set-Cookie`** response headers, set the [`credentials`](/en-US/docs/Web/API/RequestInit#credentials) option, which can take one of the following three values:

[119] - `omit`: never send credentials in the request or include credentials in the response.
- `same-origin` (the default): only send and include credentials for same-origin requests.
- `include`: always include credentials, even cross-origin.

[120] Note that if a cookie's [`SameSite`](/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#samesitesamesite-value) attribute is set to `Strict` or `Lax`, then the cookie will not be sent cross-site, even if `credentials` is set to `include`.

[121] Including credentials in cross-origin requests can make a site vulnerable to {{glossary("CSRF")}} attacks, so even if `credentials` is set to `include`, the server must also agree to their inclusion by including the {{httpheader("Access-Control-Allow-Credentials")}} header in its response.

[122] Additionally, in this situation the server must explicitly specify the client's origin in the {{httpheader("Access-Control-Allow-Origin")}} response header (that is, `*` is not allowed).

[123] This means that if `credentials` is set to `include` and the request is cross-origin, then:

[124] - If the request is a [simple request](/en-US/docs/Web/HTTP/Guides/CORS#simple_requests), then the request will be sent with credentials, but the server must set the {{httpheader("Access-Control-Allow-Credentials")}} and {{httpheader("Access-Control-Allow-Origin")}} response headers, or the browser will return a network error to the caller.

[125] If the server does set the correct headers, then the response, including credentials, will be delivered to the caller.

[126] - If the request is not a simple request, then the browser will send a [preflighted request](/en-US/docs/Web/HTTP/Guides/CORS#preflighted_requests) without credentials, and the server must set the {{httpheader("Access-Control-Allow-Credentials")}} and {{httpheader("Access-Control-Allow-Origin")}} response headers, or the browser will return a network error to the caller.

[127] If the server does set the correct headers, then the browser will follow up with the real request, including credentials, and will deliver the real response, including credentials, to the caller.

## Grouping X

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–5 (3 sentences)
  G4: sentences 6–7 (2 sentences)
  G5: sentences 8 (1 sentence)
  G6: sentences 9–10 (2 sentences)
  G7: sentences 11–14 (4 sentences)
  G8: sentences 15 (1 sentence)
  G9: sentences 16 (1 sentence)
  G10: sentences 17–19 (3 sentences)
  G11: sentences 20 (1 sentence)
  G12: sentences 21–26 (6 sentences)
  G13: sentences 27 (1 sentence)
  G14: sentences 28 (1 sentence)
  G15: sentences 29 (1 sentence)
  G16: sentences 30 (1 sentence)
  G17: sentences 31–39 (9 sentences)
  G18: sentences 40–42 (3 sentences)
  G19: sentences 43–51 (9 sentences)
  G20: sentences 52 (1 sentence)
  G21: sentences 53–63 (11 sentences)
  G22: sentences 64 (1 sentence)
  G23: sentences 65 (1 sentence)
  G24: sentences 66–67 (2 sentences)
  G25: sentences 68–69 (2 sentences)
  G26: sentences 70–77 (8 sentences)
  G27: sentences 78 (1 sentence)
  G28: sentences 79–87 (9 sentences)
  G29: sentences 88–89 (2 sentences)
  G30: sentences 90–91 (2 sentences)
  G31: sentences 92 (1 sentence)
  G32: sentences 93–94 (2 sentences)
  G33: sentences 95 (1 sentence)
  G34: sentences 96–101 (6 sentences)
  G35: sentences 102 (1 sentence)
  G36: sentences 103–104 (2 sentences)
  G37: sentences 105–106 (2 sentences)
  G38: sentences 107 (1 sentence)
  G39: sentences 108–111 (4 sentences)
  G40: sentences 112 (1 sentence)
  G41: sentences 113 (1 sentence)
  G42: sentences 114 (1 sentence)
  G43: sentences 115 (1 sentence)
  G44: sentences 116 (1 sentence)
  G45: sentences 117 (1 sentence)
  G46: sentences 118 (1 sentence)
  G47: sentences 119 (1 sentence)
  G48: sentences 120 (1 sentence)
  G49: sentences 121–122 (2 sentences)
  G50: sentences 123 (1 sentence)
  G51: sentences 124–125 (2 sentences)
  G52: sentences 126–127 (2 sentences)

## Grouping Y

  G1: sentences 1 (1 sentence)
  G2: sentences 2 (1 sentence)
  G3: sentences 3–5 (3 sentences)
  G4: sentences 6–7 (2 sentences)
  G5: sentences 8 (1 sentence)
  G6: sentences 9–10 (2 sentences)
  G7: sentences 11–13 (3 sentences)
  G8: sentences 14 (1 sentence)
  G9: sentences 15 (1 sentence)
  G10: sentences 16 (1 sentence)
  G11: sentences 17–19 (3 sentences)
  G12: sentences 20 (1 sentence)
  G13: sentences 21–23 (3 sentences)
  G14: sentences 24–26 (3 sentences)
  G15: sentences 27 (1 sentence)
  G16: sentences 28 (1 sentence)
  G17: sentences 29–30 (2 sentences)
  G18: sentences 31–33 (3 sentences)
  G19: sentences 34–36 (3 sentences)
  G20: sentences 37–39 (3 sentences)
  G21: sentences 40–42 (3 sentences)
  G22: sentences 43–45 (3 sentences)
  G23: sentences 46–47 (2 sentences)
  G24: sentences 48–49 (2 sentences)
  G25: sentences 50–51 (2 sentences)
  G26: sentences 52 (1 sentence)
  G27: sentences 53–55 (3 sentences)
  G28: sentences 56–57 (2 sentences)
  G29: sentences 58 (1 sentence)
  G30: sentences 59–60 (2 sentences)
  G31: sentences 61–63 (3 sentences)
  G32: sentences 64 (1 sentence)
  G33: sentences 65 (1 sentence)
  G34: sentences 66 (1 sentence)
  G35: sentences 67 (1 sentence)
  G36: sentences 68–69 (2 sentences)
  G37: sentences 70–72 (3 sentences)
  G38: sentences 73–75 (3 sentences)
  G39: sentences 76–77 (2 sentences)
  G40: sentences 78 (1 sentence)
  G41: sentences 79–80 (2 sentences)
  G42: sentences 81–83 (3 sentences)
  G43: sentences 84–86 (3 sentences)
  G44: sentences 87 (1 sentence)
  G45: sentences 88–90 (3 sentences)
  G46: sentences 91 (1 sentence)
  G47: sentences 92 (1 sentence)
  G48: sentences 93–95 (3 sentences)
  G49: sentences 96–97 (2 sentences)
  G50: sentences 98–100 (3 sentences)
  G51: sentences 101 (1 sentence)
  G52: sentences 102 (1 sentence)
  G53: sentences 103–104 (2 sentences)
  G54: sentences 105–106 (2 sentences)
  G55: sentences 107 (1 sentence)
  G56: sentences 108–110 (3 sentences)
  G57: sentences 111 (1 sentence)
  G58: sentences 112 (1 sentence)
  G59: sentences 113 (1 sentence)
  G60: sentences 114–115 (2 sentences)
  G61: sentences 116 (1 sentence)
  G62: sentences 117–118 (2 sentences)
  G63: sentences 119 (1 sentence)
  G64: sentences 120 (1 sentence)
  G65: sentences 121–122 (2 sentences)
  G66: sentences 123 (1 sentence)
  G67: sentences 124–125 (2 sentences)
  G68: sentences 126–127 (2 sentences)
