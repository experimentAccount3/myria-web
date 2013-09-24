from raco import RACompiler
from raco.language import MyriaAlgebra
from raco.myrialang import compile_to_json
from raco.viz import plan_to_dot
from google.appengine.ext.webapp import template
import json
import myria
import os.path
import urllib
import webapp2

defaultquery = """A(x) :- R(x,3)"""
hostname = "vega.cs.washington.edu"
port = 1776

def programplan(query, target):
    dlog = RACompiler()

    dlog.fromDatalog(query)
    return dlog.logicalplan

def format_rule(expressions):
    return "\n".join(["%s = %s" % e for e in expressions])

def get_datasets(connection=None):
    if connection is None:
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
        except myria.MyriaError:
            return []
    try:
        return connection.datasets()
    except myria.MyriaError:
        return []

def get_queries(connection=None):
    if connection is None:
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
        except myria.MyriaError:
            return []
    try:
        return connection.queries()
    except myria.MyriaError:
        return []

def get_schema_map(datasets=None, connection=None):
    if datasets is None:
        datasets = get_datasets(connection)
    return { d['relation_key']['relation_name'] : zip(d['schema']['column_names'], d['schema']['column_types']) for d in datasets}

class RedirectToEditor(webapp2.RequestHandler):
    def get(self, query=None):
        if query is not None:
            self.redirect("/editor?query=%s" % urllib.quote(query, ''), True)
        else:
            self.redirect("/editor", True)

class MyriaPage(webapp2.RequestHandler):
    def get_connection_string(self, connection=None):
        try:
            if connection is None:
                connection = myria.MyriaConnection(hostname=hostname, port=port)
            workers = connection.workers()
            alive = connection.workers_alive()
            connection_string = "%s:%d [%d/%d]" % (hostname, port, len(alive), len(workers))
        except myria.MyriaError:
            connection_string = "unable to connect to %s:%d" % (hostname, port)
        return connection_string

def nano_to_str(elapsed):
    if elapsed is None:
        return None
    s = elapsed / 1000000000.0
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    elapsed_str = ' %fs' % s
    if m > 0:
        elapsed_str = '%dm ' % m + elapsed_str
    if h > 0:
        elapsed_str = '%dh ' % h + elapsed_str
    if d > 0:
        elapsed_str = '%dd ' % d + elapsed_str
    return elapsed_str

class Queries(MyriaPage):
    def get(self):
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
            queries = connection.queries()
        except myria.MyriaError:
            connection = None
            queries = []

        for q in queries:
            q['elapsed_str'] = nano_to_str(q['elapsed_nanos'])
            if q['status'] == 'KILLED':
                q['bootstrap_status'] = 'danger'
            elif q['status'] == 'SUCCESS':
                q['bootstrap_status'] = 'success'
            elif q['status'] == 'RUNNING':
                q['bootstrap_status'] = 'warning'
            else:
                q['bootstrap_status'] = ''

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        connection_string = self.get_connection_string(connection)
        # .. load and render the template
        path = os.path.join(os.path.dirname(__file__), 'templates/queries.html')
        self.response.out.write(template.render(path, locals()))


class Datasets(MyriaPage):
    def get(self):
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
            datasets = connection.datasets()
        except myria.MyriaError:
            connection = None
            datasets = []

        for d in datasets:
            try:
                d['query_url'] = 'http://%s:%d/query/query-%d' % (hostname, port, d['query_id'])
            except:
                pass

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        connection_string = self.get_connection_string(connection)
        # .. load and render the template
        path = os.path.join(os.path.dirname(__file__), 'templates/datasets.html')
        self.response.out.write(template.render(path, locals()))


class Editor(MyriaPage):
    def get(self, query=defaultquery):
        dlog = RACompiler()
        dlog.fromDatalog(query)
        plan = format_rule(dlog.logicalplan)
        dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)
        myria_plan = format_rule(dlog.physicalplan)

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        connection_string = self.get_connection_string()
        # .. load and render the template
        path = os.path.join(os.path.dirname(__file__), 'templates/editor.html')
        self.response.out.write(template.render(path, locals()))

class Plan(webapp2.RequestHandler):
    def get(self):
        query = self.request.get("query")
        dlog = RACompiler()
        dlog.fromDatalog(query)
        plan = format_rule(dlog.logicalplan)

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write(plan)

class Optimize(webapp2.RequestHandler):
    def get(self):
        query = self.request.get("query")

        dlog = RACompiler()
        dlog.fromDatalog(query)

        dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)

        optimized = format_rule(dlog.physicalplan)

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write(optimized)

class Compile(webapp2.RequestHandler):
    def get(self):
        query = self.request.get("query")

        dlog = RACompiler()
        dlog.fromDatalog(query)
        # Cache logical plan
        cached_logicalplan = str(dlog.logicalplan)

        # Generate physical plan
        dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)

        # Get the schema map for compiling the query
        schema_map = get_schema_map()
        # .. and compile it
        try:
            compiled = compile_to_json(query, cached_logicalplan, dlog.physicalplan, schema_map)
            self.response.headers['Content-Type'] = 'application/json'
            self.response.write(json.dumps(compiled))
            return
        except ValueError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write("Error 400 (Bad Request): %s" % str(e))
            self.response.status = 400
            return

class Execute(webapp2.RequestHandler):
    def post(self):
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
        except myria.MyriaError:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write("Error 503 (Service Unavailable): Unable to connect to REST server to issue query")
            self.response.status = 503
            return

        query = self.request.get("query")

        dlog = RACompiler()
        dlog.fromDatalog(query)
        # Cache logical plan
        cached_logicalplan = str(dlog.logicalplan)

        # Generate physical plan
        dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)

        # Get the schema map for compiling the query
        schema_map = get_schema_map(connection=connection)
        # .. and compile
        try:
            compiled = compile_to_json(query, cached_logicalplan, dlog.physicalplan, schema_map)
        except ValueError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write("Error 400 (Bad Request): %s" % str(e))
            self.response.status = 400
            return

        # Issue the query
        try:
            query_status = connection.submit_query(compiled)
            query_url = 'http://%s:%d/execute?query_id=%d' % (hostname, port, query_status['query_id'])
            ret = {'query_status' : query_status, 'url' : query_url}
            self.response.status = 201
            self.response.headers['Content-Type'] = 'application/json'
            self.response.headers['Content-Location'] = query_url
            self.response.write(json.dumps(ret))
            return
        except myria.MyriaError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.status = 400
            self.response.write("Error 400 (Bad Request): %s" % str(e))
            return

    def get(self):
        try:
            connection = myria.MyriaConnection(hostname=hostname, port=port)
        except myria.MyriaError:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.status = 503
            self.response.write("Error 503 (Service Unavailable): Unable to connect to REST server to issue query")
            return

        query_id = self.request.get("query_id")

        try:
            query_status = connection.get_query_status(query_id)
            self.response.headers['Content-Type'] = 'application/json'
            ret = {'query_status' : query_status, 'url' : self.request.url}
            self.response.write(json.dumps(ret))
        except myria.MyriaError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write(e)


class Dot(webapp2.RequestHandler):
    def get(self):
        query = self.request.get("query")
        svg_type = self.request.get("type")

        dlog = RACompiler()
        dlog.fromDatalog(query)

        if svg_type is None or len(svg_type) == 0 or svg_type.lower() == "ra":
            plan = dlog.logicalplan
        elif svg_type.lower() == "myria":
            dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)
            plan = dlog.physicalplan
        else:
            self.abort(400, detail="argument type expected 'ra' or 'myria'")

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write(plan_to_dot(plan))

app = webapp2.WSGIApplication([
   ('/', RedirectToEditor),
   ('/editor', Editor),
   ('/queries', Queries),
   ('/datasets', Datasets),
   ('/plan', Plan),
   ('/optimize', Optimize),
   ('/compile', Compile),
   ('/execute', Execute),
   ('/dot', Dot)
  ],
  debug=True
)

"""
TODO: 
Debug conditions: A(x,z) :- R(x,p1,y),R(y,p2,z),R(z,p3,w)
Multiple rules
Recursion
Show graph visually
Protobuf
Show parse errors (with link to error)
"""
