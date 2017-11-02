#!/usr/bin/env python3

from collections import defaultdict

import networkx as nx
import pandas as pd
import numpy as np

from pgmpy.models import BayesianModel
from pgmpy.models.MarkovModel import MarkovModel


class IntervalTemporalBayesianNetwork(BayesianModel):
    """
    Base class for interval temporal bayesian network model. (ITBN)

    A model stores nodes and edges with conditional probability
    distribution (cpd) and other attributes.

    models hold directed edges.  Self loops are not allowed neither
    multiple (parallel) edges.

    Nodes can be any hashable python object.

    Edges are represented as links between nodes.
    """
    temporal_node_marker = "t_"
    start_time_marker = "_s"
    end_time_marker = "_e"

    def __init__(self, ebunch=None):
        super(IntervalTemporalBayesianNetwork, self).__init__(ebunch)
        self.relation_map = None
        self.event_nodes = None
        self.interval_relation_map = self.load_interval_relation_map()

    def add_edge(self, u, v, **kwargs):
        """
        Add an edge between u and v.

        The nodes u and v will be automatically added if they are
        not already in the graph

        Parameters
        ----------
        u,v : nodes
              Nodes can be any hashable python object.
        """
        super(IntervalTemporalBayesianNetwork, self).add_edge(u, v, **kwargs)

    def remove_node(self, node):
        """
        Remove node from the model.

        Removing a node also removes all the associated edges, all associated temporal nodes,
        removes the CPD of the node and marginalizes the CPDs of it's children.

        Parameters
        ----------
        node : node
            Node which is to be removed from the model.

        Returns
        -------
        None
        """
        affected_nodes = [v for u, v in self.edges() if u == node]

        for affected_node in affected_nodes:
            node_cpd = self.get_cpds(node=affected_node)
            if node_cpd:
                node_cpd.marginalize([node], inplace=True)

        if self.get_cpds(node=node):
            self.remove_cpds(node)
        super(BayesianModel, self).remove_node(node)

        for affected_node in affected_nodes:
            if affected_node.startswith(self.temporal_node_marker):
                self.remove_node(affected_node)

    def remove_nodes_from(self, nodes):
        """
        Remove multiple nodes from the model.

        Removing a node also removes all the associated edges, removes the CPD
        of the node and marginalizes the CPDs of it's children.

        Parameters
        ----------
        nodes : list, set (iterable)
            Nodes which are to be removed from the model.

        Returns
        -------
        None
        """
        for node in nodes:
            self.remove_node(node)

    def check_model(self):
        """
        Check the model for various errors. This method checks for the following
        errors.

        * Checks if the sum of the probabilities for each state is equal to 1 (tol=0.01).
        * Checks if the CPDs associated with nodes are consistent with their parents.

        Returns
        -------
        check: boolean
            True if all the checks are passed
        """
        return super(IntervalTemporalBayesianNetwork, self).check_model()

    def to_markov_model(self):
        """
        Converts bayesian model to markov model. The markov model created would
        be the moral graph of the bayesian model.
        """
        moral_graph = self.moralize()
        mm = MarkovModel(moral_graph.edges())
        mm.add_nodes_from(moral_graph.nodes())
        mm.add_factors(*[cpd.to_factor() for cpd in self.cpds])

        return mm

    def to_junction_tree(self):
        """
        Creates a junction tree (or clique tree) for a given bayesian model.

        For converting a Bayesian Model into a Clique tree, first it is converted
        into a Markov one.

        For a given markov model (H) a junction tree (G) is a graph
        1. where each node in G corresponds to a maximal clique in H
        2. each sepset in G separates the variables strictly on one side of the
        edge to other.
        """
        mm = self.to_markov_model()
        return mm.to_junction_tree()

    def fit(self, data, estimator=None, state_names=None, complete_samples_only=True, **kwargs):
        """
        Estimates the CPD for each variable based on a given data set.

        Parameters
        ----------
        data: pandas DataFrame object
            DataFrame object with column names identical to the variable names of the network.
            (If some values in the data are missing the data cells should be set to `numpy.NaN`.
            Note that pandas converts each column containing `numpy.NaN`s to dtype `float`.)

        estimator: Estimator class
            One of:
            - MaximumLikelihoodEstimator (default)
            - BayesianEstimator: In this case, pass 'prior_type' and either 'pseudo_counts'
                or 'equivalent_sample_size' as additional keyword arguments.
                See `BayesianEstimator.get_parameters()` for usage.

        state_names: dict (optional)
            A dict indicating, for each variable, the discrete set of states
            that the variable can take. If unspecified, the observed values
            in the data set are taken to be the only possible states.

        complete_samples_only: bool (default `True`)
            Specifies how to deal with missing data, if present. If set to `True` all rows
            that contain `np.Nan` somewhere are ignored. If `False` then, for each variable,
            every row where neither the variable nor its parents are `np.NaN` is used.
        """

        if state_names is None:
            state_names = []
        from pgmpy.estimators import MaximumLikelihoodEstimator, BaseEstimator

        if estimator is None:
            estimator = MaximumLikelihoodEstimator
        else:
            if not issubclass(estimator, BaseEstimator):
                raise TypeError("Estimator object should be a valid pgmpy estimator.")

        _estimator = estimator(self, data, state_names=state_names,
                               complete_samples_only=complete_samples_only)
        cpds_list = _estimator.get_parameters(**kwargs)
        self.add_cpds(*cpds_list)

    def predict(self, data):
        """
        Predicts states of all the missing variables.

        Parameters
        ----------
        data : pandas DataFrame object
            A DataFrame object with column names same as the variables in the model.
        """
        from pgmpy.inference import VariableElimination

        if set(data.columns) == set(self.nodes()):
            raise ValueError("No variable missing in data. Nothing to predict")

        elif set(data.columns) - set(self.nodes()):
            raise ValueError("Data has variables which are not in the model")

        missing_variables = list(set(self.nodes()) - set(data.columns))
        pred_values = defaultdict(list)

        # Send state_names dict from one of the estimated CPDs to the inference class.
        model_inference = VariableElimination(self, state_names=self.get_cpds()[0].state_names)
        for index, data_point in data.iterrows():
            states_dict = model_inference.map_query(variables=missing_variables,
                                                    evidence=data_point.to_dict())
            for k, v in states_dict.items():
                pred_values[k].append(v)
        return pd.DataFrame(pred_values, index=data.index)

    def predict_probability(self, data):
        """
        Predicts probabilities of all states of the missing variables.

        Parameters
        ----------
        data : pandas DataFrame object
            A DataFrame object with column names same as the variables in the model.
        """
        from pgmpy.inference import VariableElimination

        if set(data.columns) == set(self.nodes()):
            raise ValueError("No variable missing in data. Nothing to predict")

        elif set(data.columns) - set(self.nodes()):
            raise ValueError("Data has variables which are not in the model")

        missing_variables = list(set(self.nodes()) - set(data.columns))
        pred_values = defaultdict(list)

        model_inference = VariableElimination(self)
        for index, data_point in data.iterrows():
            states_dict = model_inference.query(variables=missing_variables,
                                                evidence=data_point.to_dict())
            for k, v in states_dict.items():
                for l in range(len(v.values)):
                    state = self.get_cpds(k).state_names[k][l]
                    pred_values[k + '_' + str(state)].append(v.values[l])
        return pd.DataFrame(pred_values, index=data.index)

    def is_iequivalent(self, model):
        """
        Checks whether the given model is I-equivalent

        Two graphs G1 and G2 are said to be I-equivalent if they have same skeleton
        and have same set of immoralities.

        Note: For same skeleton different names of nodes can work but for immoralities
        names of nodes must be same

        Parameters
        ----------
        model : A Bayesian model object, for which you want to check I-equivalence

        Returns
        --------
        boolean : True if both are I-equivalent, False otherwise
        """
        if not isinstance(model, IntervalTemporalBayesianNetwork):
            raise TypeError('model must be an instance of ITBN Model')
        skeleton = nx.algorithms.isomorphism.GraphMatcher(self.to_undirected(),
                                                          model.to_undirected())
        if skeleton.is_isomorphic() and self.get_immoralities() == model.get_immoralities():
            return True
        return False

    def learn_temporal_relationships(self, data):
        relation_map = dict()
        if self.event_nodes is None:
            self.event_nodes = set(
                [node for node in self.nodes() if not node.startswith(self.temporal_node_marker)])
        for node_a in self.event_nodes:
            for node_b in self.event_nodes:
                relation_set = set()
                if not node_a == node_b:
                    for sample in data.itertuples():
                        relation = self.calculate_relationship(sample, node_a, node_b)
                        relation_set.add(relation)
                relation_map[(node_a, node_b)] = sorted(relation_set)
        self.relation_map = relation_map

    def calculate_relationship(self, sample, node_a, node_b):
        start_a = getattr(sample, node_a + self.start_time_marker)
        end_a = getattr(sample, node_a + self.end_time_marker)
        start_b = getattr(sample, node_b + self.start_time_marker)
        end_b = getattr(sample, node_b + self.end_time_marker)
        temp_distance = (np.sign(start_b - start_a), np.sign(end_b - end_a),
                         np.sign(start_b - end_a), np.sign(end_b - start_a))
        return self.interval_relation_map[temp_distance]

    @staticmethod
    def load_interval_relation_map():
        interval_relation_map = dict()
        interval_relation_map[(-1., -1., -1., -1.)] = 'b'
        interval_relation_map[(1., 1., 1., 1.)] = 'bi'
        interval_relation_map[(1., -1., -1., 1.)] = 'd'
        interval_relation_map[(-1., 1., -1., 1.)] = 'di'
        interval_relation_map[(-1., -1., -1., 1.)] = 'o'
        interval_relation_map[(1., 1., -1., 1.)] = 'oi'
        interval_relation_map[(-1., -1., -1., 0.)] = 'm'
        interval_relation_map[(1., 1., 0., 1.)] = 'mi'
        interval_relation_map[(0., -1., -1., 1.)] = 's'
        interval_relation_map[(0., 1., -1., 1.)] = 'si'
        interval_relation_map[(1., 0., -1., 1.)] = 'f'
        interval_relation_map[(-1., 0., -1., 1.)] = 'fi'
        interval_relation_map[(0., 0., -1., 1.)] = 'eq'
        return interval_relation_map
